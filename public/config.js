var SEGMENTS = [
  { key: 'morning',   label: 'Morning',   range: '09:00 – 12:00', shortRange: '<12',   start: 0,  end: 12 },
  { key: 'afternoon', label: 'Afternoon', range: '12:00 – 16:00', shortRange: '12–16', start: 12, end: 16 },
  { key: 'evening',   label: 'Evening',   range: '16:00 – 19:00', shortRange: '16–19', start: 16, end: 19 },
  { key: 'late',      label: 'Late',      range: '19:00+',        shortRange: '19+',   start: 19, end: 30 },
];

// Store details (address, coordinates, website, notes) keyed by display
// name. Filled at runtime from api/v1/madrid/stores.json, which the
// pipeline generates from the store registry (data/stores.json) — edit
// stores there.
var STORE_META = {};

var GAME_CLASS_MAP = {
  'Magic: The Gathering': 'game-mtg',
  'Star Wars: Unlimited': 'game-starwars',
  'Riftbound':            'game-riftbound',
  'One Piece':            'game-onepiece',
  'Pokémon':              'game-pokemon',
  'Lorcana':              'game-lorcana',
  'Digimon':              'game-digimon',
  'Yu-Gi-Oh!':            'game-yugioh',
  'Flesh and Blood':      'game-fab',
  'Final Fantasy TCG':    'game-finalfantasy',
  'Weiß Schwarz':         'game-weiss',
  'Naruto Mythos':        'game-naruto',
  'Cyberpunk TCG':        'game-cyberpunk',
  'Gundam Card Game':     'game-gundam',
  'Dragon Ball Fusion World':  'game-dbfw',
  'Dragon Ball Super Masters': 'game-dbmasters',
  'My Little Pony':       'game-mlp',
  'Palworld':             'game-palworld',
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
