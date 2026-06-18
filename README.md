# A Public Deepwoken Database

This is a publicly available 'database' of talents, mantras, weapons and other information from the game [Deepwoken](https://www.roblox.com/games/4111023553) needed for people to develop and maintain tools.

## Contributing

Whether it's adding new talent entries, fixing a requirement or even fixing a typo, all contributions are accepted and encouraged.

## Structure

Data is stored in a simple, flat structure, kinda like SQL tables. Each folder is a 'table', and `.json` files are each a singular 'row'.

There is planned support for cross table references, but I don't see too much use for it right now.

## Using the Data

If you're a developer trying to access the data, API wrappers are provided in the following repository for the following languages:

- Typescript (via [npm](https://www.npmjs.com/package/deepwoken))
- Rust (via [cargo](https://crates.io/crates/deepwoken))

You do not need an API key, as the wrappers pull data straight from this repo.

### internal TODO
make data validation verify:
- any prerequisites used must actually exist in here
- verify correctenss of the maintained api wrappers
