/* Stable Home Energy loader protocol 1. Only reads the local managed dashboard. */
const load = async () => {
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const response = await fetch(new URL('./version.json', import.meta.url), {cache:'no-store', credentials:'same-origin'});
      if (!response.ok) throw new Error('Version unavailable');
      const version = await response.json();
      if (version.schema !== 1 || !/^[0-9a-f]{64}$/.test(version.sha256)
          || version.entry !== `card-${version.sha256}.js`) throw new Error('Invalid local dashboard release');
      await import(new URL(version.entry, import.meta.url).href);
      return;
    } catch (error) {
      // Retry once if an atomic deployment removed the previous content-addressed file.
      if (attempt === 1) throw error;
    }
  }
};
try { await load(); }
catch {
  if (!customElements.get('home-energy-tesla-dashboard')) {
    customElements.define('home-energy-tesla-dashboard', class extends HTMLElement {
      setConfig() { this.textContent = 'Dashboard kon niet worden geladen. Controleer Observer en ververs deze pagina.'; }
      set hass(value) { /* No controls when release could not be loaded. */ }
      getCardSize() { return 2; }
    });
  }
}
