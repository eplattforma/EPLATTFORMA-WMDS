/**
 * AdminTrackingRoster.tsx — per-user activity-tracking roster
 *
 * Deliberately shows `role` beside the toggle WITHOUT using it for anything.
 * In your production data 9 users record shifts across 3 roles (5 picker,
 * 2 admin, 2 warehouse_manager), so role does not predict who should be
 * tracked. Keeping the column visible makes that mismatch obvious to whoever
 * is administering the list.
 *
 * GET  /api/activity/roster
 * POST /api/activity/roster/:username   { enabled }
 * POST /api/activity/master-switch      { enabled }
 */

import { useCallback, useEffect, useState } from 'react';

interface RosterRow {
  username: string;
  display_name: string;
  role: string;
  is_active: boolean;
  track_activity: boolean;
  effective: boolean;
  track_activity_set_by: string | null;
  track_activity_set_at: string | null;
  shifts_recorded: number;
  last_shift: string | null;
}

const api = async (url: string, body?: unknown) => {
  const res = await fetch(url, {
    method: body ? 'POST' : 'GET',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error ?? res.statusText);
  return res.json();
};

export function AdminTrackingRoster() {
  const [rows, setRows] = useState<RosterRow[]>([]);
  const [master, setMaster] = useState<boolean | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data: RosterRow[] = await api('/api/activity/roster');
      setRows(data);
      // effective = master AND track_activity AND is_active, so any row where
      // the user flag is on but effective is off implies the master switch is off.
      const anyFlagged = data.find((r) => r.track_activity && r.is_active);
      setMaster(anyFlagged ? anyFlagged.effective : null);
      setError(null);
    } catch (e) { setError((e as Error).message); }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const toggleUser = async (r: RosterRow) => {
    setBusy(r.username); setError(null);
    try {
      await api(`/api/activity/roster/${encodeURIComponent(r.username)}`,
        { enabled: !r.track_activity });
      await load();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(null); }
  };

  const toggleMaster = async () => {
    setBusy('__master'); setError(null);
    try {
      await api('/api/activity/master-switch', { enabled: !master });
      await load();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(null); }
  };

  const tracked = rows.filter((r) => r.track_activity && r.is_active).length;
  const roles = [...new Set(rows.filter((r) => r.track_activity).map((r) => r.role))];

  return (
    <div className="roster">
      <header className="roster__head">
        <div>
          <h2>Activity tracking</h2>
          <p>
            {tracked} user{tracked === 1 ? '' : 's'} tracked across{' '}
            {roles.length} role{roles.length === 1 ? '' : 's'} ({roles.join(', ') || 'none'}).
            Tracking is per user — role is shown for context only and is never used as a gate.
          </p>
        </div>
        <button
          className={`master ${master ? 'on' : 'off'}`}
          onClick={toggleMaster}
          disabled={busy === '__master'}
        >
          {busy === '__master' ? '…' : master ? 'System ON' : 'System OFF'}
        </button>
      </header>

      {master === false && (
        <div className="banner">
          Master switch is off — nobody is being tracked regardless of the toggles below.
        </div>
      )}
      {error && <div className="banner banner--error">{error}</div>}

      <table>
        <thead>
          <tr>
            <th>User</th><th>Role</th><th className="num">Shifts</th>
            <th>Last shift</th><th>Tracked</th><th>Set by</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.username} className={r.is_active ? '' : 'inactive'}>
              <td>
                <b>{r.display_name}</b>
                {!r.is_active && <span className="tag">account disabled</span>}
              </td>
              <td className="muted">{r.role}</td>
              <td className="num">{r.shifts_recorded || '—'}</td>
              <td className="muted">
                {r.last_shift ? new Date(r.last_shift).toLocaleDateString() : '—'}
              </td>
              <td>
                <button
                  className={`toggle ${r.track_activity ? 'on' : 'off'}`}
                  onClick={() => void toggleUser(r)}
                  // A disabled account can never be tracked (picker_tracking_enabled
                  // requires is_active), so don't offer a toggle that does nothing.
                  disabled={busy === r.username || !r.is_active}
                  title={!r.is_active ? 'Account is disabled — cannot be tracked' : undefined}
                >
                  {busy === r.username ? '…' : r.track_activity ? 'On' : 'Off'}
                </button>
              </td>
              <td className="muted small">
                {r.track_activity_set_by ?? '—'}
                {r.track_activity_set_at &&
                  ` · ${new Date(r.track_activity_set_at).toLocaleDateString()}`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <footer className="roster__foot">
        Turning tracking <b>off</b> closes any open shift for that user immediately.
        Turning it <b>on</b> takes effect at their next check-in — elapsed time
        cannot be retroactively covered. Every change is written to
        <code> user_tracking_audit</code>.
      </footer>
    </div>
  );
}

export default AdminTrackingRoster;
