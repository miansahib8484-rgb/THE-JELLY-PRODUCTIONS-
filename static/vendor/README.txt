Third-party front-end libraries, vendored so the site works offline and never runs code from a CDN.

bootstrap 5.3.3   (MIT)  https://www.npmjs.com/package/bootstrap/v/5.3.3   css + bundle js (source map comments removed)
chart.js 4.4.3    (MIT)  https://www.npmjs.com/package/chart.js/v/4.4.3     dist/chart.umd.js

To upgrade: `npm pack bootstrap@X chart.js@Y`, copy the same files into a new versioned folder, update the {% static %} paths in
templates/base.html, templates/dashboard/base.html and the dashboard templates that load Chart.js.
Google Fonts (text fonts and the Material Symbols icon font) are still loaded from fonts.googleapis.com / fonts.gstatic.com.
