export function createUpgradeCard(): HTMLElement {
  const wrap = document.createElement("aside");
  wrap.className = "upgrade";

  wrap.innerHTML = `
    <h3>This is Compa Lite</h3>
    <p>The web version is one-device-at-a-time, control surface only. The full Compa box on a Raspberry Pi gives you:</p>
    <ul>
      <li>Multi-device USB hub — 404 + P-6 + Twister together</li>
      <li>Ableton Link sync to the rest of your studio</li>
      <li>Link Audio bridge into Live (network audio)</li>
      <li>Network MIDI host to your Mac</li>
      <li>Always-on companion that doesn't sleep mid-set</li>
    </ul>
    <a href="https://raredata.net/compa" target="_blank" rel="noopener">Get the Compa box →</a>
  `;
  return wrap;
}
