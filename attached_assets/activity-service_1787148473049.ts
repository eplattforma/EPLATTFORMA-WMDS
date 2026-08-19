/**
 * activity-service.ts — data layer for gapless picker timeline
 *
 * STACK NOTE: written for Node + node-postgres because Replit + Neon implies it.
 * If you are on Python/Go instead, this file is a ~1 hour port: every function
 * below is a single SQL call. All invariants (no gaps, no overlaps, idempotency,
 * grace window, resolution) live in Postgres functions, NOT here — so a port
 * cannot accidentally weaken them.
 *
 * Requires migrations 010 + 011.
 */

import type { Pool, PoolClient } from 'pg';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type PickerState =
  | 'picking' | 'break' | 'restock' | 'assist' | 'repacking'
  | 'awaiting_order' | 'unassigned' | 'offline';

/** States a picker may choose. Never includes unassigned/offline — those are
 *  system-assigned and must not be selectable. */
export const DECLARABLE: PickerState[] = [
  'picking', 'break', 'restock', 'assist', 'repacking',
];

export type Resolution =
  | 'not_required' | 'pending' | 'classified_live' | 'classified_retro'
  | 'declined' | 'supervisor_set' | 'written_off';

export interface Segment {
  id: string;
  shift_id: number;
  picker_username: string;
  state: PickerState;
  started_at: string;
  ended_at: string | null;
  duration_sec: number | null;
  resolution: Resolution;
}

export interface OpenShift {
  shift_id: number;
  picker_username: string;
  check_in_time: string;
  open_segment_id: string | null;
  open_state: PickerState | null;
  open_started_at: string | null;
}

export class TrackingDisabledError extends Error {
  code = 'TRACKING_DISABLED' as const;
}
/** Client acted on a segment the server has already closed (e.g. the reaper
 *  got there first). Client must re-sync rather than retry. */
export class StaleSegmentError extends Error {
  code = 'STALE_SEGMENT' as const;
}

// ---------------------------------------------------------------------------
// Service
// ---------------------------------------------------------------------------

export class ActivityService {
  constructor(private pool: Pool) {}

  private async tx<T>(fn: (c: PoolClient) => Promise<T>): Promise<T> {
    const c = await this.pool.connect();
    try {
      await c.query('BEGIN');
      const out = await fn(c);
      await c.query('COMMIT');
      return out;
    } catch (e) {
      await c.query('ROLLBACK');
      throw e;
    } finally {
      c.release();
    }
  }

  /** THE gate. Never check `role` anywhere in the app — call this. */
  async trackingEnabled(username: string): Promise<boolean> {
    const { rows } = await this.pool.query(
      'SELECT picker_tracking_enabled($1) AS ok', [username],
    );
    return rows[0]?.ok === true;
  }

  private async assertEnabled(username: string) {
    if (!(await this.trackingEnabled(username))) {
      throw new TrackingDisabledError(`Activity tracking is not enabled for ${username}`);
    }
  }

  // -------------------------------------------------------------------------
  // Shift lifecycle
  // -------------------------------------------------------------------------

  /**
   * Check in. Creates the shift AND opens the first segment atomically, so the
   * timeline is covered from check_in_time with no window in which time is
   * unowned. The client should immediately show the activity picker; anything
   * chosen within the grace window relabels this segment back to check-in.
   */
  async checkIn(username: string, coords?: string): Promise<OpenShift> {
    await this.assertEnabled(username);
    return this.tx(async (c) => {
      const existing = await c.query(
        `SELECT id FROM shifts
          WHERE picker_username = $1 AND check_out_time IS NULL
          ORDER BY check_in_time DESC LIMIT 1`, [username],
      );
      let shiftId: number;
      if (existing.rowCount) {
        shiftId = existing.rows[0].id;             // idempotent re-check-in
      } else {
        const ins = await c.query(
          `INSERT INTO shifts (picker_username, check_in_time, check_in_coordinates, status)
           VALUES ($1, now()::timestamp, $2, 'active') RETURNING id`,
          [username, coords ?? null],
        );
        shiftId = ins.rows[0].id;
        await c.query('SELECT picker_shift_open($1)', [shiftId]);
      }
      return this.readOpenShift(c, shiftId);
    });
  }

  /**
   * Check out. Closes the open segment at check_out_time — no segment is ever
   * left dangling. Returns blocks still needing a label; the picker leaves
   * regardless, the RECORD stays open.
   */
  async checkOut(username: string, shiftId: number, coords?: string) {
    return this.tx(async (c) => {
      await c.query('SELECT picker_shift_close($1, now()::timestamp, $2)', [shiftId, 'check_out']);
      if (coords) {
        await c.query('UPDATE shifts SET check_out_coordinates=$2 WHERE id=$1', [shiftId, coords]);
      }
      const { rows } = await c.query(
        `SELECT id, started_at, ended_at, ROUND(duration_sec/60.0,1) AS minutes
           FROM picker_segment
          WHERE shift_id=$1 AND resolution IN ('pending','declined')
          ORDER BY started_at`, [shiftId],
      );
      if (rows.length) {
        await c.query(`UPDATE shifts SET status='pending_review' WHERE id=$1`, [shiftId]);
      }
      return { shiftId, unresolved: rows };
    });
  }

  private async readOpenShift(c: PoolClient | Pool, shiftId: number): Promise<OpenShift> {
    const { rows } = await c.query(
      `SELECT s.id AS shift_id, s.picker_username, s.check_in_time,
              g.id AS open_segment_id, g.state AS open_state, g.started_at AS open_started_at
         FROM shifts s
         LEFT JOIN picker_segment g ON g.shift_id=s.id AND g.ended_at IS NULL
        WHERE s.id=$1`, [shiftId],
    );
    return rows[0];
  }

  /** Current shift + open segment. Client calls this on load and after any 409. */
  async currentShift(username: string): Promise<OpenShift | null> {
    const { rows } = await this.pool.query(
      `SELECT s.id AS shift_id, s.picker_username, s.check_in_time,
              g.id AS open_segment_id, g.state AS open_state, g.started_at AS open_started_at
         FROM shifts s
         LEFT JOIN picker_segment g ON g.shift_id=s.id AND g.ended_at IS NULL
        WHERE s.picker_username=$1 AND s.check_out_time IS NULL
        ORDER BY s.check_in_time DESC LIMIT 1`, [username],
    );
    return rows[0] ?? null;
  }

  // -------------------------------------------------------------------------
  // Transitions
  // -------------------------------------------------------------------------

  /**
   * The ONLY way to change state. Closes the open segment and opens the new one
   * at the same instant, in one transaction — a gap is not expressible.
   *
   * @param actionId  client-generated UUID. Replaying it returns the original
   *                  segment and creates nothing (kills double-taps/retries).
   * @param expectedOpenSegmentId  optimistic lock; mismatch throws StaleSegmentError.
   */
  async transition(opts: {
    shiftId: number;
    username: string;
    newState: PickerState;
    actionId: string;
    openedBy?: 'picker' | 'system';
    closeReason?: string;
    expectedOpenSegmentId?: string | null;
  }): Promise<{ segmentId: string }> {
    await this.assertEnabled(opts.username);
    try {
      const { rows } = await this.pool.query(
        `SELECT picker_transition($1,$2::picker_state,now()::timestamp,$3,$4,$5::uuid,$6) AS id`,
        [
          opts.shiftId, opts.newState, opts.openedBy ?? 'picker',
          opts.closeReason ?? 'declared', opts.actionId,
          opts.expectedOpenSegmentId ?? null,
        ],
      );
      return { segmentId: rows[0].id };
    } catch (e: any) {
      // 55000 = serialization_failure, raised by picker_transition on stale id
      if (e?.code === '55000' || /stale_segment/.test(e?.message ?? '')) {
        throw new StaleSegmentError('Segment already closed — re-sync');
      }
      throw e;
    }
  }

  /** Order finished. Server-side switch to unassigned so no time is unowned
   *  while the picker decides. The modal then labels it. */
  async packingComplete(shiftId: number, username: string, actionId: string) {
    return this.transition({
      shiftId, username, newState: 'unassigned', actionId,
      openedBy: 'system', closeReason: 'packing_complete',
    });
  }

  /** Picker asked for work and the queue was empty. Attributed to Planning,
   *  never scored against the picker. */
  async awaitingOrder(shiftId: number, username: string, actionId: string) {
    return this.transition({
      shiftId, username, newState: 'awaiting_order', actionId, openedBy: 'system',
    });
  }

  /** Every ~30s from the client. Drives crash detection: the reaper closes a
   *  dead device's segment at last_heartbeat, not at reaper-run time. */
  async heartbeat(shiftId: number) {
    await this.pool.query(
      'UPDATE shifts SET last_heartbeat_at=now()::timestamp WHERE id=$1', [shiftId],
    );
  }

  // -------------------------------------------------------------------------
  // Resolution — nothing silently vanishes
  // -------------------------------------------------------------------------

  async unresolvedForShift(shiftId: number) {
    const { rows } = await this.pool.query(
      `SELECT id, started_at, ended_at, ROUND(duration_sec/60.0,1) AS minutes, resolution
         FROM picker_segment
        WHERE shift_id=$1 AND resolution IN ('pending','declined')
        ORDER BY started_at`, [shiftId],
    );
    return rows;
  }

  async classify(segmentId: string, state: PickerState, by: string) {
    await this.pool.query('SELECT picker_classify_segment($1,$2::picker_state,$3)',
      [segmentId, state, by]);
  }

  /** Picker cannot recall. Honest — and it ESCALATES rather than closing. */
  async decline(segmentId: string, by: string) {
    await this.pool.query('SELECT picker_decline_segment($1,$2)', [segmentId, by]);
  }

  async supervisorResolve(segmentId: string, state: PickerState | null, by: string, note?: string) {
    await this.pool.query('SELECT picker_supervisor_resolve($1,$2::picker_state,$3,$4)',
      [segmentId, state, by, note ?? null]);
  }

  async reviewQueue() {
    const { rows } = await this.pool.query('SELECT * FROM vw_supervisor_review_queue');
    return rows;
  }

  // -------------------------------------------------------------------------
  // Admin — per-user roster
  // -------------------------------------------------------------------------

  async roster() {
    const { rows } = await this.pool.query('SELECT * FROM vw_tracking_roster');
    return rows;
  }

  /** Turning tracking OFF closes any open shift cleanly (DB trigger).
   *  Turning it ON takes effect at the next check-in — elapsed time cannot be
   *  retroactively covered. */
  async setTracking(username: string, enabled: boolean, adminUsername: string) {
    await this.pool.query(
      `UPDATE users SET track_activity=$2, track_activity_set_by=$3 WHERE username=$1`,
      [username, enabled, adminUsername],
    );
  }

  async masterSwitch(enabled: boolean) {
    await this.pool.query(
      `UPDATE settings SET value=$1 WHERE key='activity_mode.enabled'`, [String(enabled)],
    );
  }

  // -------------------------------------------------------------------------
  // Health — wire these to your monitoring. Non-empty means something broke.
  // -------------------------------------------------------------------------

  async integrityCheck() {
    const [gaps, recon] = await Promise.all([
      this.pool.query('SELECT * FROM vw_shift_timeline_integrity'),
      this.pool.query('SELECT * FROM vw_accounting_reconciliation'),
    ]);
    return {
      healthy: gaps.rowCount === 0 && recon.rowCount === 0,
      timelineViolations: gaps.rows,
      reconciliationBreaks: recon.rows,
    };
  }

  /** Cron: every 5 minutes. */
  async reapStaleShifts() {
    const { rows } = await this.pool.query('SELECT * FROM picker_reap_stale_shifts()');
    return rows;
  }

  /** Cron: daily. */
  async autoWriteOff() {
    const { rows } = await this.pool.query('SELECT picker_auto_writeoff_stale() AS n');
    return rows[0].n as number;
  }
}
