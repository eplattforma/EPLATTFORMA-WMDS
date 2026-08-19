/**
 * activity-routes.ts — HTTP layer for the gapless picker timeline
 *
 * Mount:  app.use('/api/activity', makeActivityRoutes(pool))
 *
 * Every handler is a thin wrapper over ActivityService. No business rule lives
 * here — the invariants are in Postgres. Deliberately so: an HTTP handler that
 * forgets a check cannot create a gap.
 *
 * DELETE from your codebase when adopting this:
 *   - every `role === 'picker'` / `role !== 'picker'` check
 *   - the hardcoded DEDICATED_PICKERS list
 * Replace with: svc.trackingEnabled(username)
 */

import { Router, type Request, type Response, type NextFunction } from 'express';
import type { Pool } from 'pg';
import {
  ActivityService, DECLARABLE, StaleSegmentError, TrackingDisabledError,
  type PickerState,
} from './activity-service.js';

/** Replace with your real auth. Must yield the authenticated username + role. */
interface AuthedRequest extends Request {
  user?: { username: string; role: string };
}

const SUPERVISOR_ROLES = new Set(['admin', 'warehouse_manager']);

export function makeActivityRoutes(pool: Pool): Router {
  const svc = new ActivityService(pool);
  const r = Router();

  const wrap = (fn: (req: AuthedRequest, res: Response) => Promise<unknown>) =>
    async (req: Request, res: Response, next: NextFunction) => {
      try { await fn(req as AuthedRequest, res); } catch (e) { next(e); }
    };

  const me = (req: AuthedRequest): string => {
    const u = req.user?.username;
    if (!u) throw Object.assign(new Error('Unauthenticated'), { status: 401 });
    return u;
  };

  const requireSupervisor = (req: AuthedRequest) => {
    if (!SUPERVISOR_ROLES.has(req.user?.role ?? '')) {
      throw Object.assign(new Error('Supervisor role required'), { status: 403 });
    }
    return me(req);
  };

  /** Validate against DECLARABLE, not the full enum: 'unassigned' and 'offline'
   *  are system-assigned and must never be selectable by a client. */
  const declarable = (v: unknown): PickerState => {
    if (typeof v !== 'string' || !DECLARABLE.includes(v as PickerState)) {
      throw Object.assign(
        new Error(`state must be one of: ${DECLARABLE.join(', ')}`), { status: 400 });
    }
    return v as PickerState;
  };

  /** Express types route params as `string | string[]`; narrow to a scalar. */
  const param = (req: AuthedRequest, name: string): string => {
    const v = (req.params as Record<string, string | string[]>)[name];
    const s = Array.isArray(v) ? v[0] : v;
    if (typeof s !== 'string' || !s) {
      throw Object.assign(new Error(`missing path parameter: ${name}`), { status: 400 });
    }
    return s;
  };

  const actionId = (req: AuthedRequest): string => {
    const id = req.body?.action_id;
    if (typeof id !== 'string' || !/^[0-9a-f-]{36}$/i.test(id)) {
      throw Object.assign(
        new Error('action_id (UUID v4) is required — it is what makes retries safe'),
        { status: 400 });
    }
    return id;
  };

  // ---------------------------------------------------------------- session

  /** Client calls this on load. Drives whether to show shift controls at all. */
  r.get('/session', wrap(async (req, res) => {
    const username = me(req);
    const [enabled, shift] = await Promise.all([
      svc.trackingEnabled(username),
      svc.currentShift(username),
    ]);
    res.json({ username, tracking_enabled: enabled, shift, declarable: DECLARABLE });
  }));

  r.post('/check-in', wrap(async (req, res) => {
    const shift = await svc.checkIn(me(req), req.body?.coordinates);
    // 201 + immediately prompt the activity picker on the client.
    res.status(201).json({ shift, prompt_activity: true });
  }));

  r.post('/check-out', wrap(async (req, res) => {
    const out = await svc.checkOut(me(req), Number(req.body?.shift_id), req.body?.coordinates);
    res.json({
      ...out,
      // The picker always leaves. The RECORD stays open until resolved.
      shift_status: out.unresolved.length ? 'pending_review' : 'completed',
    });
  }));

  r.post('/heartbeat', wrap(async (req, res) => {
    await svc.heartbeat(Number(req.body?.shift_id));
    res.json({ ok: true });
  }));

  // ------------------------------------------------------------ transitions

  r.post('/transition', wrap(async (req, res) => {
    const out = await svc.transition({
      shiftId: Number(req.body?.shift_id),
      username: me(req),
      newState: declarable(req.body?.state),
      actionId: actionId(req),
      openedBy: 'picker',
      closeReason: 'declared',
      expectedOpenSegmentId: req.body?.expected_open_segment_id ?? null,
    });
    res.json(out);
  }));

  r.post('/packing-complete', wrap(async (req, res) => {
    const out = await svc.packingComplete(
      Number(req.body?.shift_id), me(req), actionId(req));
    // Time is already owned server-side; the modal only labels it.
    res.json({ ...out, prompt_activity: true });
  }));

  r.post('/awaiting-order', wrap(async (req, res) => {
    const out = await svc.awaitingOrder(
      Number(req.body?.shift_id), me(req), actionId(req));
    res.json(out);
  }));

  // ------------------------------------------------------------- resolution

  r.get('/unresolved/:shiftId', wrap(async (req, res) => {
    res.json(await svc.unresolvedForShift(Number(param(req,'shiftId'))));
  }));

  r.post('/segment/:id/classify', wrap(async (req, res) => {
    await svc.classify(param(req,'id'), declarable(req.body?.state), me(req));
    res.json({ ok: true });
  }));

  /** "Can't recall" — escalates, does NOT close. */
  r.post('/segment/:id/decline', wrap(async (req, res) => {
    await svc.decline(param(req,'id'), me(req));
    res.json({ ok: true, escalated: true });
  }));

  // ------------------------------------------------------------- supervisor

  r.get('/review-queue', wrap(async (req, res) => {
    requireSupervisor(req);
    res.json(await svc.reviewQueue());
  }));

  r.post('/segment/:id/resolve', wrap(async (req, res) => {
    const by = requireSupervisor(req);
    // state === null is an explicit write-off, so don't run it through declarable()
    const state = req.body?.state === null ? null : declarable(req.body?.state);
    await svc.supervisorResolve(param(req,'id'), state, by, req.body?.note);
    res.json({ ok: true });
  }));

  // ------------------------------------------------------------------ admin

  r.get('/roster', wrap(async (req, res) => {
    requireSupervisor(req);
    res.json(await svc.roster());
  }));

  r.post('/roster/:username', wrap(async (req, res) => {
    const by = requireSupervisor(req);
    if (typeof req.body?.enabled !== 'boolean') {
      throw Object.assign(new Error('enabled (boolean) required'), { status: 400 });
    }
    await svc.setTracking(param(req,'username'), req.body.enabled, by);
    res.json({ ok: true });
  }));

  r.post('/master-switch', wrap(async (req, res) => {
    requireSupervisor(req);
    await svc.masterSwitch(Boolean(req.body?.enabled));
    res.json({ ok: true });
  }));

  // ----------------------------------------------------------------- health

  /** Wire to monitoring. healthy:false means an invariant broke — page someone. */
  r.get('/health', wrap(async (_req, res) => {
    const h = await svc.integrityCheck();
    res.status(h.healthy ? 200 : 500).json(h);
  }));

  // ------------------------------------------------------------ error shape

  r.use((err: any, _req: Request, res: Response, _next: NextFunction) => {
    if (err instanceof StaleSegmentError) {
      // Client must re-fetch /session, not retry blindly.
      return res.status(409).json({ error: err.message, code: err.code, action: 'resync' });
    }
    if (err instanceof TrackingDisabledError) {
      return res.status(403).json({ error: err.message, code: err.code });
    }
    const status = err?.status ?? 500;
    if (status >= 500) console.error('[activity]', err);
    res.status(status).json({ error: err?.message ?? 'Internal error' });
  });

  return r;
}
