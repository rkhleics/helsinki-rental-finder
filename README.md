# Helsinki Rental Finder

## Disclaimer

This code is provided as-is, it was used by the author to find an apartment in Helsinki and should not be considered production ready. The script was not written with readability or performance in mind.

The script was run on an x86-64 machine running Manjaro Linux, other platforms have not been tested.

## Prerequisites

You will need a Digitransit API key to use the HSL API https://digitransit.fi/en/developers/

## Installation

[Poetry](https://python-poetry.org/) is used for dependency management, install it with `pip install poetry`.

1. Install dependencies

   `$ poetry install`

2. Copy the example environment file

   `$ cp .env.example .env`

3. Edit the environment file and add your Digitransit API key

   `$ vim .env`

## Usage

`$ poetry run ./scrape.py`

This will create a html file in `/tmp/` containing your search results.
