import ui from './ui/tr'

/**
 * Turkish texts. `sport.<slug>` names the sports of the registry (`GET /api/v1/sports` gives each its
 * `i18n_key`); everything else of the web UI is under `ui` (./ui/tr.ts). The texts of the classic views
 * went with them (FX-14b).
 */
export default {
  sport: {
    football: 'Futbol',
    basketball: 'Basketbol',
    tennis: 'Tenis',
    'american-football': 'Amerikan futbolu',
    'aussie-rules': 'Avustralya futbolu',
    'ice-hockey': 'Buz hokeyi',
    handball: 'Hentbol',
    rugby: 'Ragbi',
    futsal: 'Futsal',
    minifootball: 'Mini futbol',
    floorball: 'Florbol',
    volleyball: 'Voleybol',
    badminton: 'Badminton',
    'table-tennis': 'Masa tenisi',
    padel: 'Padel',
    snooker: 'Snooker',
    baseball: 'Beyzbol',
    cricket: 'Kriket',
    esports: 'E-spor',
    darts: 'Dart',
    mma: 'MMA',
  },
  // The web UI (src/app, src/ui, src/screens)
  ui,
}
