var STORE_ADDRESSES = {
  'Arte 9': 'Calle de Francisco Silvela, 21, 28028 Madrid',
  'Asedio Gaming': 'Calle Soria 5, Bajo Izquierda, Las Rozas, 28231',
  'Generacion X - Elfo': 'Elfo 98, 28027 Madrid',
  'Goblintrader Madrid-Norte': 'C. del Marqués de Lema 7, local 3, 28003 Madrid',
  'Jupiter Juegos': 'Jupiter Madrid',
  'La Guarida Juegos': 'Calle de las Peñuelas, 14, Local 1, 28005 Madrid',
  'Metropolis Center': 'Calle Andrés Mellado, 22, 28015 Madrid',
  'Micelion Games': 'Avenida del Mediterráneo, 8, 28007 Madrid',
  'Ítaca': 'Ítaca, Madrid',
};

var SEGMENTS = [
  { key: 'morning',   label: 'Morning',   range: '09:00 – 12:00', shortRange: '<12',   start: 0,  end: 12 },
  { key: 'afternoon', label: 'Afternoon', range: '12:00 – 16:00', shortRange: '12–16', start: 12, end: 16 },
  { key: 'evening',   label: 'Evening',   range: '16:00 – 19:00', shortRange: '16–19', start: 16, end: 19 },
  { key: 'late',      label: 'Late',      range: '19:00+',        shortRange: '19+',   start: 19, end: 30 },
];

var STORE_META = {
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
  'Ítaca': {
    address: 'Calle del Pez, 20, 28004 Madrid',
    notes: 'Malasaña',
  },
  'Kamikaze Freak Shop': {
    address: 'Calle Ponferrada, entre el 11 y el 15, 28039 Madrid',
    website: 'https://kamikazefreakshop.es/',
  },
  'Metamorfo': {
    address: 'Calle Ilustración 3, 28902 Getafe',
    website: 'https://metamorfo.es/',
  },
  'Panda Games': {
    address: 'Paseo de la Castellana 194, 28046 Madrid',
    website: 'https://pandagames.es/',
  },
  'The Big Bang Games': {
    address: 'C/ Entre Arroyos 1, Local 6, 28030 Madrid',
    website: 'https://www.thebigbanggames.com/',
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
