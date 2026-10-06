import type tr from './tr'
import ui from './ui/en'

/**
 * English texts, with the keys of ./tr.ts. `sport.<slug>` names the sports of the registry; everything
 * else of the web UI is under `ui` (./ui/en.ts). The texts of the classic views went with them (FX-14b).
 */
const en: typeof tr = {
  sport: {
    football: 'Football',
    basketball: 'Basketball',
    tennis: 'Tennis',
    'american-football': 'American football',
    'aussie-rules': 'Aussie rules',
    'ice-hockey': 'Ice hockey',
    handball: 'Handball',
    rugby: 'Rugby',
    futsal: 'Futsal',
    minifootball: 'Minifootball',
    floorball: 'Floorball',
    volleyball: 'Volleyball',
    badminton: 'Badminton',
    'table-tennis': 'Table tennis',
    padel: 'Padel',
    snooker: 'Snooker',
    baseball: 'Baseball',
    cricket: 'Cricket',
    esports: 'E-sports',
    darts: 'Darts',
    mma: 'MMA',
  },
  // The web UI (src/app, src/ui, src/screens)
  ui,
}

export default en
