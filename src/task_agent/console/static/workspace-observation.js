(() => {
  function observationStatus(snapshot, refreshFailed = false, now = Date.now()) {
    if (!snapshot) return { stale: false, leaseExpired: false, label: 'Not checked', source: 'No observation' };
    const checkedAt = Date.parse(snapshot.checkedAt);
    const age = now - checkedAt;
    const lifetime = snapshot.freshForSeconds;
    const stale = refreshFailed || !Number.isFinite(age) || age < 0 || !Number.isFinite(lifetime) || lifetime <= 0 || age >= lifetime * 1000;
    const expiry = Date.parse(snapshot.expiresAt);
    return {
      stale,
      leaseExpired: Number.isFinite(expiry) && expiry <= now,
      label: stale ? 'Observation out of date / recheck status' : 'Fresh Azure observation',
      source: `${stale ? 'Last Azure check' : 'Azure query'} / ${Number.isFinite(checkedAt) ? snapshot.checkedAt : 'unknown time'}`,
    };
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { observationStatus };
  else window.workspaceObservation = { observationStatus };
})();