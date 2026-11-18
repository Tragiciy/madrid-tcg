// Time-of-day slots and day sections live in model.js (TCG.TIME_SLOTS,
// TCG.DAY_SECTIONS), next to the logic that uses them.

// Short game names for chips and pills (the data keeps the full name).
var GAME_SHORT = {
  'Magic: The Gathering':      'Magic',
  'Star Wars: Unlimited':      'Star Wars',
  'Dragon Ball Fusion World':  'Dragon Ball FW',
  'Dragon Ball Super Masters': 'DB Masters',
  'Final Fantasy TCG':         'Final Fantasy',
  'Gundam Card Game':          'Gundam',
  'Cyberpunk TCG':             'Cyberpunk',
};

// Store details (address, coordinates, website, notes) keyed by display
// name. Filled at runtime from api/v1/madrid/stores.json, which the
// pipeline generates from the store registry (data/stores.json) — edit
// stores there.
var STORE_META = {};

// Games with their own colour (the 8 busiest; styles.css section 1). Any
// other game gets 'game-other', the warm neutral.
var GAME_CLASS_MAP = {
  'Magic: The Gathering':     'game-mtg',
  'One Piece':                'game-onepiece',
  'Pokémon':                  'game-pokemon',
  'Riftbound':                'game-riftbound',
  'Lorcana':                  'game-lorcana',
  'Digimon':                  'game-digimon',
  'Gundam Card Game':         'game-gundam',
  'Dragon Ball Fusion World': 'game-dbfw',
};

// Map view (Leaflet, loaded on first open). Tiles are the standard
// OpenStreetMap ones: free and fine at this site's traffic under the OSM tile
// usage policy (attribution shown, no prefetching). If traffic grows, point
// `url` at another raster provider; nothing else needs to change.
var MAP_TILES = {
  url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  maxZoom: 19,
};
var MAP_DEFAULT_VIEW = { lat: 40.4168, lng: -3.7038, zoom: 11 };  // Madrid
