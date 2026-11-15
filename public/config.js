var STORE_ADDRESSES = {
  'Arte 9': 'Calle de Francisco Silvela, 21, 28028 Madrid',
  'Asedio Gaming': 'Calle Soria 5, Bajo Izquierda, Las Rozas, 28231',
  'Generacion X - Elfo': 'Elfo 98, 28027 Madrid',
  'Goblintrader Madrid-Norte': 'C. del Marqués de Lema 7, local 3, 28003 Madrid',
  'Jupiter Juegos': 'Jupiter Madrid',
  'La Guarida Juegos': 'Calle de las Peñuelas, 14, Local 1, 28005 Madrid',
  'Metropolis Center': 'Calle Andrés Mellado, 22, 28015 Madrid',
  'Micelion Games': 'Avenida del Mediterráneo, 8, 28007 Madrid',
  'Next Dice': 'Calle de Jorge Manrique S/N, Local 19, Algete, Madrid, 28110',
  'OZ JUEGOS': 'P.º de Fuente Lucha 13, Alcobendas, 28100 Madrid',
  'Replay Boardgame Cafe': 'Calle de la Ribera de Curtidores, 26, Local 3, 28005 Madrid',
  'Three Stones Games': 'Pl. del Toro, Centro Comercial La Rotonda, 28760 Tres Cantos, Madrid',
  'Ítaca': 'Ítaca, Madrid',
};

var SEGMENTS = [
  { key: 'morning',   label: 'Morning',   range: '09:00 – 12:00', shortRange: '<12',   start: 0,  end: 12 },
  { key: 'afternoon', label: 'Afternoon', range: '12:00 – 16:00', shortRange: '12–16', start: 12, end: 16 },
  { key: 'evening',   label: 'Evening',   range: '16:00 – 19:00', shortRange: '16–19', start: 16, end: 19 },
  { key: 'late',      label: 'Late',      range: '19:00+',        shortRange: '19+',   start: 19, end: 30 },
];

var STORE_META = {
  'Wargen Wargames': {
    address: 'Avenida de las Águilas, 2B, Local 2, 28044 Madrid',
    website: 'https://wargenwargames.com/',
  },
  'Arte 9': {
    address: 'Calle de Francisco Silvela, 21, 28028 Madrid',
    website: 'https://arte9.es',
    notes: 'Near Diego de León metro (L4, L5, L6)',
  },
  'Asedio Gaming': {
    address: 'Calle Soria 5, Bajo Izquierda, Las Rozas, 28231',
    website: 'https://asediogaming.com/',
  },
  'Generacion X - Elfo': {
    address: 'Elfo 98, 28027 Madrid',
    website: 'https://genexcomics.com/',
  },
  'Goblintrader Madrid-Norte': {
    address: 'C. del Marqués de Lema 7, local 3, 28003 Madrid',
    website: 'https://www.goblintrader.es',
  },
  'Jupiter Juegos': {
    address: 'Calle de la Cruz, 10, 28012 Madrid',
    notes: 'Sol / Gran Vía area',
  },
  'La Guarida Juegos': {
    address: 'Calle de las Peñuelas, 14, Local 1, 28005 Madrid',
  },
  'Metropolis Center': {
    address: 'Calle Andrés Mellado, 22, 28015 Madrid',
  },
  'Micelion Games': {
    address: 'Avenida del Mediterráneo, 8, 28007 Madrid',
  },
  'Next Dice': {
    address: 'Calle de Jorge Manrique S/N, Local 19, Algete, Madrid, 28110',
    website: 'https://nextdice.es/',
  },
  'OZ JUEGOS': {
    address: 'P.º de Fuente Lucha 13, Alcobendas, 28100 Madrid',
    website: 'https://www.ozjuegos.com/',
  },
  'Replay Boardgame Cafe': {
    address: 'Calle de la Ribera de Curtidores, 26, Local 3, 28005 Madrid',
    website: 'https://www.replayoutletcafe.com/',
  },
  'Three Stones Games': {
    address: 'Pl. del Toro, Centro Comercial La Rotonda, 28760 Tres Cantos, Madrid',
    website: 'https://threestonesgames.com/',
  },
  'Ítaca': {
    address: 'Calle Blasco de Garay, 15, Local 1, 28015 Madrid',
    notes: 'Malasaña',
  },
  'Kamikaze Freak Shop': {
    address: 'Calle Ponferrada, 11, 28029 Madrid',
    website: 'https://kamikazefreakshop.es/',
  },
  'Metamorfo': {
    address: 'Calle Ilustración 3, 28902 Getafe',
    website: 'https://metamorfo.es/',
  },
  'Panda Games': {
    address: 'CC X-Madrid, planta alta, Calle Oslo, 53, 28922 Alcorcón',
    website: 'https://pandagames.es/',
  },
  'The Big Bang Games': {
    address: 'C/ Entre Arroyos 1, Local 6, 28030 Madrid',
    website: 'https://www.thebigbanggames.com/',
  },
  'Collectorage': {
    address: 'Calle Hernani, 19, 28020 Madrid',
    website: 'https://collectorage.com/',
  },
  'TopDeck': {
    address: 'Calle del Cardenal Silíceo, 17, 28002 Madrid',
    website: 'https://topdeck.es/',
    notes: 'Barrio de Prosperidad, near Avenida de América',
  },
  'Padis': {
    address: 'Calle Blasco de Garay, 36, 28015 Madrid',
    website: 'https://www.padis-store.com/',
  },
  'MADAKIBA': {
    address: 'Calle Afrodita, 5, Local 7, 28342 Valdemoro',
    website: 'https://madakiba.com/',
  },
  'Goblintrader Central': {
    address: 'Calle de la Montera, 12, 28013 Madrid',
    website: 'https://www.goblintrader.es/',
  },
  'Gladius Games': {
    address: 'Calle Cánovas del Castillo, 5, Local 6, 28807 Alcalá de Henares',
    website: 'https://gladiusgames.net/',
  },
  'Mundicomics': {
    address: 'Calle Ramón y Cajal, 3, Local 1, 28850 Torrejón de Ardoz',
    website: 'https://www.mundicomics.com/',
  },
};

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
};
