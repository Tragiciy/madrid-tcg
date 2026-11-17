# discoverers/ — store discovery modules, separate from scrapers/.
# Each module must expose a discover() -> list[dict] function.
#
# TODO(multi-city): discovery still searches the Madrid area only (each
# module's own centre, or shared.cities.default()). Looping over
# shared.cities.CITIES also needs a `city` on every candidate and in
# tools/discover_stores.py's matching and output (data/candidate_stores.json).
# The daily pipeline does not depend on it: locator scrapers learn the
# stores of every city into data/stores.json on their own.
