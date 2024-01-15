#! /usr/bin/env python
import json
import re
import textwrap
import time
from datetime import datetime
from urllib.parse import urlencode

import geopandas as gpd
import pandas as pd
import requests
from environs import Env
from finscraper.request import SeleniumCallbackRequest
from finscraper.scrapy_spiders.oikotieapartment import (
    _OikotieApartmentItem,
    _OikotieApartmentSpider,
)
from finscraper.spiders import _get_docstring
from finscraper.wrappers import _SpiderWrapper
from geopy import distance
from itemloaders.processors import Identity, TakeFirst
from jinja2 import Environment, FileSystemLoader
from loguru import logger
from requests.adapters import HTTPAdapter
from scrapy import Field
from scrapy.http import HtmlResponse
from scrapy.linkextractors import LinkExtractor
from scrapy.loader import ItemLoader
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from tqdm import tqdm
from urllib3.util.retry import Retry

from selenium import webdriver
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.core.os_manager import ChromeType
from selenium.webdriver.support import expected_conditions as EC
from finscraper import utils
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService

env = Env()
env.read_env()

pd.set_option("display.max_colwidth", None)
pd.set_option("display.max_rows", None)

tqdm.pandas()

DISTANCE_LIMIT = 5000  # meters

INTERESTNG_LOCATIONS = [(60.1628905, 24.9198913)]


def get_chromedriver(options=None, settings=None):
    """Get chromedriver automatically.

    Args:
        options (selenium.webdriver.chrome.options.Options, optional):
            Options to start chromedriver with. If None, will use default
            settings. Defaults to None.
        settings (scrapy.settings.Settings, optional): Scrapy settings to
            take into consideration when starting chromedriver. If None,
            will not be taken into consideration. Defaults to None.

    Returns:
        Selenium webdriver for Chrome (selenium.webdriver.Chrome).
    """
    settings = settings or {}
    if options is None:
        options = Options()
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-dev-shm-usage")
        options.add_experimental_option("prefs", {"intl.accept_languages": "fi,fi_FI"})
        if not settings.get("DISABLE_HEADLESS", False):
            options.add_argument("--headless")
        if settings.get("PROGRESS_BAR_ENABLED", True):
            options.add_argument("--disable-logging")

    service = ChromeService(
        ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install()
    )
    driver = webdriver.Chrome(service=service, options=options)
    if settings.get("MINIMIZE_WINDOW", False):
        try:
            driver.minimize_window()
        except WebDriverException:
            pass

    return driver


utils.get_chromedriver = get_chromedriver


class MyOikotieApartmentItem(_OikotieApartmentItem):
    latitude = Field(input_processor=Identity(), output_processor=TakeFirst())
    longitude = Field(input_processor=Identity(), output_processor=TakeFirst())
    published = Field(input_processor=Identity(), output_processor=TakeFirst())


class _OikotieRentalSpider(_OikotieApartmentSpider):
    base_url = "https://asunnot.oikotie.fi/vuokra-asunnot"
    item_link_extractor = LinkExtractor(
        allow_domains=("asunnot.oikotie.fi"),
        allow=(r".*\/vuokra-asunnot\/.*\/[0-9]{3,}"),
        deny=(r".*?origin\=.*"),
        deny_domains=(),
        canonicalize=True,
    )

    custom_settings = {
        **_OikotieApartmentSpider.custom_settings,
        # Scrapy
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 0.2,
        "CONCURRENT_REQUESTS": 1,
        "RETRY_HTTP_CODES": [500, 502, 503, 504, 522, 524, 408, 429, 403],
        "RETRY_TIMES": 10,
    }

    title2field = {
        **_OikotieApartmentSpider.title2field,
        "Vuokra/kk": "price_no_tax",
        # "Lemmikkieläimet sallittu": "pets_allowed",
    }

    locations = [[64, 6, "Helsinki"], [39, 6, "Espoo"], [65, 6, "Vantaa"]]

    rooms = [2, 3, 4, 5]

    def _handle_pagination_page(self, request, spider, driver):
        driver.get(request.url)

        logger.debug("Scrolling pagination page to bottom...")
        listings_xpath = '//div[contains(@class, "cards-v2__card")]'
        driver.execute_script("window.scrollTo(0,document.body.scrollHeight)")

        logger.debug("Waiting for listings to be available...")
        page = request.meta["page"]
        print(f"Waiting for listings to be available...{page}")
        n_listings = self.listings_per_page if page < self._last_page else 1
        WebDriverWait(driver, 10).until(
            lambda browser: len(browser.find_elements(By.XPATH, listings_xpath))
            >= n_listings
        )
        logger.debug("Listings rendered, returning response")

        return HtmlResponse(
            driver.current_url,
            body=driver.page_source.encode("utf-8"),
            encoding="utf-8",
            request=request,
        )

    def get_url(self, page=1):
        params = {"pagination": page, "price[max]": 1200, "size[min]": 48}
        locationstr = str(self.locations).replace(" ", "").replace("'", '"')
        roomstr = "".join(["&roomCount[]={}".format(room) for room in self.rooms])
        return f"{self.base_url}?{urlencode(params)}{roomstr}&locations={locationstr}"

    def start_requests(self):
        driver = get_chromedriver(settings=self.settings)

        base_url_with_area = self.get_url()
        logger.info(f'Using "{base_url_with_area}" as start URL')
        print(f'Using "{base_url_with_area}" as start URL')

        driver.get(base_url_with_area)

        # Click yes on modal, if it exists (Selenium)
        self._handle_start_modal(driver)

        # Find the last page in pagination
        self._last_page = self._get_last_page(driver)
        print(f"Found {self._last_page} pages in pagination")

        driver.close()

        # Iterate pagination pages one-by-one and extract links + items
        for page in range(1, self._last_page + 1):
            url = self.get_url(page)
            yield SeleniumCallbackRequest(
                url,
                priority=10,
                meta={"page": page},
                selenium_callback=self._handle_pagination_page,
            )

    def _get_last_page(self, driver):
        logger.debug("Getting last page...")
        last_page_xpath = '//span[contains(@ng-bind, "ctrl.totalPages")]'
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, last_page_xpath))
        )
        last_page_element = driver.find_elements(By.XPATH, last_page_xpath)[-1]
        logger.debug(last_page_element.text)
        last_page = int(last_page_element.text.split("/")[-1].strip())
        logger.debug(f"Last page found: {last_page}")
        return last_page

    def _parse_item(self, resp):
        il = ItemLoader(item=MyOikotieApartmentItem(), response=resp)
        il.add_value("url", resp.url)
        il.add_value("time", int(time.time()))

        # Apartment info
        il.add_xpath("title", "//title//text()")
        il.add_xpath("overview", '//div[contains(@class, "listing-overview")]//text()')

        # From tables
        table_xpath = '//dt[text()="{title}"]/following-sibling::dd[1]//text()'
        for title, field in self.title2field.items():
            il.add_xpath(field, table_xpath.format(title=title))

        # Contact information
        il.add_xpath(
            "contact_person_name",
            '//div[contains(@class, "listing-person__details-item--big")]' "//text()",
        )
        il.add_xpath(
            "contact_person_job_title",
            '//div[contains(@class, "listing-person__details-item--waisted")]'
            "//text()",
        )
        il.add_xpath(
            "contact_person_phone_number",
            '(//div[contains(@class, "listing-person__details-item'
            '--sm-top-margin")]/span)[2]//text()',
        )
        il.add_xpath(
            "contact_person_company",
            '//div[@class="listing-company__name"]/a/span//text()',
        )
        il.add_xpath("contact_person_email", "(//p)[1]//text()")
        il.add_xpath("latitude", "//meta[@property='place:location:latitude']/@content")
        il.add_xpath(
            "longitude", "//meta[@property='place:location:longitude']/@content"
        )

        pattern = r"otAsunnot=(\{.*\});"
        script = resp.xpath("//script[contains(., 'var otAsunnot')]/text()").get()
        json_data = re.search(pattern, script).group(1)
        data = json.loads(json_data)

        published = data["analytics"]["published"]

        il.add_value("published", published)
        return il.load_item()


class OikotieRental(_SpiderWrapper):
    __doc__ = _get_docstring(_OikotieRentalSpider, _OikotieApartmentItem)

    def __init__(self, area=None, jobdir=None, progress_bar=True, log_level=None):
        super().__init__(
            spider_cls=_OikotieRentalSpider,
            spider_params=dict(area=area),
            jobdir=jobdir,
            progress_bar=progress_bar,
            log_level=log_level,
        )


# an API client class for HSL's Digitransit API with retry and throttling
class DigitransitClient:
    ROUTING_URL = "https://api.digitransit.fi/routing/v1/routers/hsl/index/graphql"

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "digitransit-client"})
        self._retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        self._adapter = HTTPAdapter(max_retries=self._retry)
        self._session.mount("https://", self._adapter)
        self._session.mount("http://", self._adapter)
        self._session.headers.update(
            {"digitransit-subscription-key": env("DIGITRANSIT_API_KEY")}
        )

    # get travel time between 2 locations
    def get_travel_time(self, from_lat, from_lon, to_lat, to_lon):
        query = textwrap.dedent(
            """
            query ($from_lat: Float!, $from_lon: Float!, $to_lat: Float!, $to_lon: Float!) {
              plan(
                from: {lat: $from_lat, lon: $from_lon}
                to: {lat: $to_lat, lon: $to_lon}
                numItineraries: 1
              ) {
                itineraries {
                  duration
                  legs {
                    mode
                    duration
                    distance
                  }
                }
              }
            }
            """
        )
        response = self._session.post(
            self.ROUTING_URL,
            json={
                "query": query,
                "variables": {
                    "from_lat": from_lat,
                    "from_lon": from_lon,
                    "to_lat": to_lat,
                    "to_lon": to_lon,
                },
            },
        )
        return response.json()["data"]["plan"]["itineraries"][0]

    # get walking distance between 2 locations
    def get_walking_distance(self, from_lat, from_lon, to_lat, to_lon):
        query = textwrap.dedent(
            """
            query ($from_lat: Float!, $from_lon: Float!, $to_lat: Float!, $to_lon: Float!) {
              plan(
                from: {lat: $from_lat, lon: $from_lon}
                to: {lat: $to_lat, lon: $to_lon}
                numItineraries: 1
                modes: "WALK"
              ) {
                itineraries {
                  duration
                  walkDistance
                }
              }
            }
            """
        )
        response = self._session.post(
            self.ROUTING_URL,
            json={
                "query": query,
                "variables": {
                    "from_lat": from_lat,
                    "from_lon": from_lon,
                    "to_lat": to_lat,
                    "to_lon": to_lon,
                },
            },
        )
        return response.json()["data"]["plan"]["itineraries"][0]

    # get the nearest stop to a given location
    def get_nearest_stop(self, lat, lon, distance=500):
        query = textwrap.dedent(
            """
            query ($lat: Float!, $lon: Float!, $distance: Int!) {
                stopsByRadius(lat: $lat, lon: $lon, radius: $distance) {
                    edges {
                        node {
                            stop {
                                name
                                zoneId
                            }
                            distance
                        }
                    }
                }
            }
            """
        )
        response = self._session.post(
            self.ROUTING_URL,
            json={
                "query": query,
                "variables": {"lat": lat, "lon": lon, "distance": distance},
            },
        )
        return response.json()["data"]["stopsByRadius"]["edges"]

    def get_zones(self, lat, lon, distance=500):
        zones = set()
        for stop in self.get_nearest_stop(lat, lon, distance):
            zones.add(stop["node"]["stop"]["zoneId"])
        zones.discard(None)
        return "".join(sorted(zones))


def get_translate_url(url):
    return f"https://translate.google.com/translate?sl=auto&tl=en&u={url}"


save_dir = "/tmp/apartments"
try:
    spider = OikotieRental().load(save_dir)
except FileNotFoundError:
    spider = OikotieRental(jobdir=save_dir)
logger.info(f"Saving apartments to {save_dir}")
spider.scrape(n=100, timeout=0)
spider.save()
apartments = spider.get()

logger.info(f"Found {len(apartments)} apartments")
print(f"Found {len(apartments)} apartments")

# calculate the distance in metres between 2 points in espg:4326
apartments["distance"] = apartments.apply(
    lambda x: distance.distance(
        (x["latitude"], x["longitude"]), INTERESTNG_LOCATIONS[0]
    ).m,
    axis=1,
)

# check for ones we have seen Already
try:
    seen = pd.read_csv("oikotie-seen.csv")
except FileNotFoundError:
    pass

# drop rows with distance greater than DISTANCE_LIMIT
apartments = apartments[apartments["distance"] < DISTANCE_LIMIT].reset_index(drop=True)
logger.info(f"Found {len(apartments)} apartments within {DISTANCE_LIMIT} metres")
print(f"Found {len(apartments)} apartments within {DISTANCE_LIMIT} metres")

hsl_client = DigitransitClient()
location_y, location_x = INTERESTNG_LOCATIONS[0]


# add the walking distance and travel time to the dataframe
walking = pd.json_normalize(
    apartments.progress_apply(
        lambda x: hsl_client.get_walking_distance(
            x["latitude"], x["longitude"], location_y, location_x
        ),
        axis=1,
    )
).add_prefix("walking_")
travel = pd.json_normalize(
    apartments.progress_apply(
        lambda x: hsl_client.get_travel_time(
            x["latitude"], x["longitude"], location_y, location_x
        ),
        axis=1,
    )
).add_prefix("travel_")
stops = apartments.progress_apply(
    lambda x: hsl_client.get_zones(x["latitude"], x["longitude"], distance=500), axis=1
)
apartments["stops"] = stops

# split the walking distance and travel time into separate columns
apartments = apartments.join(walking).join(travel).reset_index(drop=True)

# replace non-floating point numbers in the life_sq column with empty string
apartments["life_sq_d"] = (
    apartments["life_sq"]
    .str.replace(",", ".")
    .apply(lambda x: re.sub(r"[^0-9.]", "", x))
)

# replace non-digits in the price_no_tax column with empty string
apartments["price_no_tax_d"] = apartments["price_no_tax"].apply(
    lambda x: re.sub(r"\D", "", x)
)

apartments["price_per_sq"] = apartments["price_no_tax_d"].astype(int) / apartments[
    "life_sq_d"
].astype(float)

apartments["walkingTime"] = apartments["walking_duration"].apply(lambda x: x / 60)
apartments["transitTime"] = apartments["travel_duration"].apply(lambda x: x / 60)
apartments["translateUrl"] = apartments["url"].apply(get_translate_url)
# make the translateUrl column a link using the last part of the url as the link text
apartments["translateUrl"] = apartments["translateUrl"].apply(
    lambda x: f"<a data-apt-id='{x.split('/')[-1]}' href='{x}' target='_blank'>{x.split('/')[-1]}</a>"
)
apartments.rename(columns={"walking_walkDistance": "walking_dist"}, inplace=True)

# create a geodataframe from the dataframe using the latitude and longitude columns
gdf = gpd.GeoDataFrame(
    apartments, geometry=gpd.points_from_xy(apartments.longitude, apartments.latitude)
)


def score(row):
    """
    Calculate a numerical score for each row based on different columns and their weights.
    Higher score is better

    Accepts:
        row: a row from the data frame
    Returns:
        score: a float score for the row
    """
    WEIGHTS = {
        # "walking_dist": -0.1,
        "walking_duration": -0.05,
        "travel_duration": -0.05,
        "price_no_tax_d": -0.05,
        "price_per_sq": -5,
        "life_sq_d": 5,
    }

    score = sum([float(row[col]) * WEIGHTS[col] for col in WEIGHTS])

    # zones A and B are the Best
    if row["stops"] in ["A", "B"]:
        score += 10

    # having a sauna is good
    if row["has_sauna"] or row["building_has_sauna"]:
        score += 10

    return score


# add the score to each row
gdf["score"] = gdf.apply(score, axis=1)


def write_output(gdf):
    """
    Accepts:
        gdf: GeoDataFrame containing the apartment data
    Outputs:
        html file containing a map with markers for each apartments
    """

    # use the output.tpl file to create a html file with the table
    env = Environment(loader=FileSystemLoader("."))
    template = env.get_template("output.tpl")

    columns = [
        "title",
        "published",
        "price_no_tax",
        "life_sq",
        "price_per_sq",
        "floor",
        "availability",
        "walkingTime",
        "walking_dist",
        "transitTime",
        "stops",
        "has_sauna",
        "building_has_sauna",
        "translateUrl",
        "score",
    ]

    # make a copy of the dataframe to avoid modifying the original
    gdf = gdf.copy()

    # style the table to have 2 decimal places on columns
    gdf["price_per_sq"] = gdf["price_per_sq"].map("{:,.2f}€/m²".format)
    gdf["walking_dist"] = gdf["walking_dist"].map("{:,.0f} m".format)
    gdf["walkingTime"] = gdf["walkingTime"].map("{:,.0f} min".format)
    gdf["transitTime"] = gdf["transitTime"].map("{:,.0f} min".format)

    gdf.sort_values("score", ascending=False, inplace=True)
    table = gdf.to_html(
        columns=columns,
        render_links=True,
        escape=False,
        index=False,
        classes=["sortable", "table", "table-striped", "table-hover", "table-sm"],
    )

    geo_columns = columns + ["geometry"]
    template_vars = {
        "title": "Oikotie Rental",
        "table": table,
        "location_x": location_x,
        "location_y": location_y,
        "data": gdf[geo_columns].to_json(),
    }
    html_out = template.render(template_vars)

    fn = f"/tmp/apartments-{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.html"
    with open(fn, "w") as f:
        f.write(html_out)
    print(f"results writteen to {fn}")


write_output(gdf)
