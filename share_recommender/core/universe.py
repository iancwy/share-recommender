"""The curated list of ETFs and shares the recommender picks from.

Each entry says which slice of a portfolio it fills (asset class) and carries tags used
for the user's exclusions. The list is deliberately short and made of large, liquid
names; it is a starting point, not a screen of every listed security.

Shares are listed roughly largest first within each market; that order is used when no
fundamentals are available to rank them.
"""

from dataclasses import dataclass, field
from enum import StrEnum


class AssetClass(StrEnum):
    AU_SHARES = "au_shares"
    GLOBAL_SHARES = "global_shares"
    EMERGING_SHARES = "emerging_shares"
    BONDS = "bonds"
    CASH = "cash"


ASSET_CLASS_LABELS: dict[AssetClass, str] = {
    AssetClass.AU_SHARES: "Australian shares",
    AssetClass.GLOBAL_SHARES: "International shares",
    AssetClass.EMERGING_SHARES: "Emerging markets shares",
    AssetClass.BONDS: "Bonds",
    AssetClass.CASH: "Cash",
}

GROWTH_CLASSES = {AssetClass.AU_SHARES, AssetClass.GLOBAL_SHARES, AssetClass.EMERGING_SHARES}


class Kind(StrEnum):
    ETF = "etf"
    SHARE = "share"


class Exclusion(StrEnum):
    FOSSIL_FUELS = "fossil_fuels"
    TOBACCO = "tobacco"
    GAMBLING = "gambling"
    WEAPONS = "weapons"
    ALCOHOL = "alcohol"


EXCLUSION_LABELS: dict[Exclusion, str] = {
    Exclusion.FOSSIL_FUELS: "Fossil fuels (oil, gas, coal)",
    Exclusion.TOBACCO: "Tobacco",
    Exclusion.GAMBLING: "Gambling",
    Exclusion.WEAPONS: "Weapons",
    Exclusion.ALCOHOL: "Alcohol",
}


class EtfStyle(StrEnum):
    BROAD = "broad"            # whole-market index; the default choice
    INCOME = "income"          # tilted to higher dividend payers
    SUSTAINABLE = "sustainable"  # screens out some industries; check its policy
    REGIONAL = "regional"      # one country's market, used only when nothing broader fits
    THEMATIC = "thematic"      # one high-growth theme; only for speculative goals


class Theme(StrEnum):
    """High-growth themes a speculative goal can invest in."""

    AI = "ai"
    CYBERSECURITY = "cybersecurity"
    CLEAN_ENERGY = "clean_energy"
    BIOTECH = "biotech"


THEME_LABELS: dict[Theme, str] = {
    Theme.AI: "AI, robotics and semiconductors",
    Theme.CYBERSECURITY: "Cybersecurity",
    Theme.CLEAN_ENERGY: "Clean energy and electric vehicles",
    Theme.BIOTECH: "Biotech",
}


@dataclass(frozen=True)
class Instrument:
    ticker: str
    exchange: str
    name: str
    kind: Kind
    asset_class: AssetClass
    sector: str | None = None
    style: EtfStyle | None = None
    excluded_by: frozenset[Exclusion] = field(default_factory=frozenset)
    themes: frozenset[Theme] = field(default_factory=frozenset)

    @property
    def key(self) -> tuple[str, str]:
        return (self.ticker, self.exchange)


def _etf(ticker, exchange, name, asset_class, style=EtfStyle.BROAD, theme=None) -> Instrument:
    return Instrument(ticker, exchange, name, Kind.ETF, asset_class, style=style,
                      themes=frozenset({theme} if theme else ()))


def _share(ticker, exchange, name, sector, *excluded_by: Exclusion,
           asset_class: AssetClass | None = None, themes: tuple[Theme, ...] = ()) -> Instrument:
    if asset_class is None:
        asset_class = AssetClass.AU_SHARES if exchange == "ASX" else AssetClass.GLOBAL_SHARES
    return Instrument(ticker, exchange, name, Kind.SHARE, asset_class, sector=sector,
                      excluded_by=frozenset(excluded_by), themes=frozenset(themes))


A, G, E, B, C = (AssetClass.AU_SHARES, AssetClass.GLOBAL_SHARES, AssetClass.EMERGING_SHARES,
                 AssetClass.BONDS, AssetClass.CASH)
FOSSIL, TOBACCO, GAMBLING, WEAPONS, ALCOHOL = (Exclusion.FOSSIL_FUELS, Exclusion.TOBACCO,
                                               Exclusion.GAMBLING, Exclusion.WEAPONS,
                                               Exclusion.ALCOHOL)

# ETFs are listed in order of preference within each asset class: ASX first so most
# purchases need no currency conversion, then other markets as fallbacks.
ETFS: list[Instrument] = [
    _etf("VAS", "ASX", "Vanguard Australian Shares Index ETF", A),
    _etf("A200", "ASX", "Betashares Australia 200 ETF", A),
    _etf("IOZ", "ASX", "iShares Core S&P/ASX 200 ETF", A),
    _etf("VHY", "ASX", "Vanguard Australian Shares High Yield ETF", A, EtfStyle.INCOME),
    _etf("FAIR", "ASX", "Betashares Australian Sustainability Leaders ETF", A,
         EtfStyle.SUSTAINABLE),
    _etf("EWA", "NYSE", "iShares MSCI Australia ETF", A),
    _etf("VGS", "ASX", "Vanguard MSCI Index International Shares ETF", G),
    _etf("IOO", "ASX", "iShares Global 100 ETF", G),
    _etf("IVV", "ASX", "iShares S&P 500 ETF", G, EtfStyle.REGIONAL),
    _etf("ETHI", "ASX", "Betashares Global Sustainability Leaders ETF", G, EtfStyle.SUSTAINABLE),
    _etf("VT", "NYSE", "Vanguard Total World Stock ETF", G),
    _etf("VTI", "NYSE", "Vanguard Total Stock Market ETF", G, EtfStyle.REGIONAL),
    _etf("VOO", "NYSE", "Vanguard S&P 500 ETF", G, EtfStyle.REGIONAL),
    _etf("ISF", "LSE", "iShares Core FTSE 100 UCITS ETF", G, EtfStyle.REGIONAL),
    _etf("XIU", "TSX", "iShares S&P/TSX 60 Index ETF", G, EtfStyle.REGIONAL),
    _etf("2800", "HKEX", "Tracker Fund of Hong Kong", G, EtfStyle.REGIONAL),
    _etf("ES3", "SGX", "SPDR Straits Times Index ETF", G, EtfStyle.REGIONAL),
    _etf("FNZ", "NZX", "Smartshares NZ Top 50 ETF", G, EtfStyle.REGIONAL),
    _etf("VGE", "ASX", "Vanguard FTSE Emerging Markets Shares ETF", E),
    _etf("IEM", "ASX", "iShares MSCI Emerging Markets ETF", E),
    _etf("VWO", "NYSE", "Vanguard FTSE Emerging Markets ETF", E),
    _etf("VAF", "ASX", "Vanguard Australian Fixed Interest Index ETF", B),
    _etf("IAF", "ASX", "iShares Core Composite Bond ETF", B),
    _etf("VGB", "ASX", "Vanguard Australian Government Bond Index ETF", B),
    _etf("BND", "NASDAQ", "Vanguard Total Bond Market ETF", B),
    _etf("AAA", "ASX", "Betashares Australian High Interest Cash ETF", C),
    _etf("BILL", "ASX", "iShares Core Cash ETF", C),
    _etf("SGOV", "NYSE", "iShares 0-3 Month Treasury Bond ETF", C),
    # Thematic: used only by speculative goals.
    _etf("RBTZ", "ASX", "Betashares Global Robotics and Artificial Intelligence ETF", G,
         EtfStyle.THEMATIC, Theme.AI),
    _etf("SEMI", "ASX", "Global X Semiconductor ETF", G, EtfStyle.THEMATIC, Theme.AI),
    _etf("HACK", "ASX", "Betashares Global Cybersecurity ETF", G, EtfStyle.THEMATIC,
         Theme.CYBERSECURITY),
    _etf("ERTH", "ASX", "Betashares Climate Change Innovation ETF", G, EtfStyle.THEMATIC,
         Theme.CLEAN_ENERGY),
    _etf("CURE", "ASX", "Global X S&P Biotech ETF", G, EtfStyle.THEMATIC, Theme.BIOTECH),
    _etf("BOTZ", "NASDAQ", "Global X Robotics & Artificial Intelligence ETF", G,
         EtfStyle.THEMATIC, Theme.AI),
    _etf("SMH", "NASDAQ", "VanEck Semiconductor ETF", G, EtfStyle.THEMATIC, Theme.AI),
    _etf("CIBR", "NASDAQ", "First Trust NASDAQ Cybersecurity ETF", G, EtfStyle.THEMATIC,
         Theme.CYBERSECURITY),
    _etf("ICLN", "NASDAQ", "iShares Global Clean Energy ETF", G, EtfStyle.THEMATIC,
         Theme.CLEAN_ENERGY),
    _etf("XBI", "NYSE", "SPDR S&P Biotech ETF", G, EtfStyle.THEMATIC, Theme.BIOTECH),
]

SHARES: list[Instrument] = [
    # ASX
    _share("CBA", "ASX", "Commonwealth Bank of Australia", "Financials"),
    _share("BHP", "ASX", "BHP Group", "Materials", FOSSIL),  # metallurgical coal mining
    _share("CSL", "ASX", "CSL", "Health Care"),
    _share("NAB", "ASX", "National Australia Bank", "Financials"),
    _share("WBC", "ASX", "Westpac Banking Corporation", "Financials"),
    _share("ANZ", "ASX", "ANZ Group Holdings", "Financials"),
    _share("WES", "ASX", "Wesfarmers", "Consumer"),
    _share("MQG", "ASX", "Macquarie Group", "Financials"),
    _share("GMG", "ASX", "Goodman Group", "Real Estate"),
    _share("RIO", "ASX", "Rio Tinto", "Materials"),
    _share("FMG", "ASX", "Fortescue", "Materials"),
    _share("WDS", "ASX", "Woodside Energy Group", "Energy", FOSSIL),
    _share("TLS", "ASX", "Telstra Group", "Communication"),
    _share("WOW", "ASX", "Woolworths Group", "Consumer Staples"),
    _share("TCL", "ASX", "Transurban Group", "Industrials"),
    _share("ALL", "ASX", "Aristocrat Leisure", "Consumer", GAMBLING),
    _share("COL", "ASX", "Coles Group", "Consumer Staples", ALCOHOL),  # owns liquor stores
    _share("QBE", "ASX", "QBE Insurance Group", "Financials"),
    _share("XRO", "ASX", "Xero", "Technology"),
    _share("STO", "ASX", "Santos", "Energy", FOSSIL),
    # United States
    _share("AAPL", "NASDAQ", "Apple", "Technology"),
    _share("MSFT", "NASDAQ", "Microsoft", "Technology"),
    _share("NVDA", "NASDAQ", "NVIDIA", "Technology", themes=(Theme.AI,)),
    _share("AMZN", "NASDAQ", "Amazon", "Consumer"),
    _share("GOOGL", "NASDAQ", "Alphabet", "Communication"),
    _share("META", "NASDAQ", "Meta Platforms", "Communication"),
    _share("JPM", "NYSE", "JPMorgan Chase", "Financials"),
    _share("V", "NYSE", "Visa", "Financials"),
    _share("UNH", "NYSE", "UnitedHealth Group", "Health Care"),
    _share("JNJ", "NYSE", "Johnson & Johnson", "Health Care"),
    _share("XOM", "NYSE", "Exxon Mobil", "Energy", FOSSIL),
    _share("PG", "NYSE", "Procter & Gamble", "Consumer Staples"),
    _share("COST", "NASDAQ", "Costco Wholesale", "Consumer Staples"),
    _share("HD", "NYSE", "Home Depot", "Consumer"),
    _share("KO", "NYSE", "Coca-Cola", "Consumer Staples"),
    _share("PEP", "NASDAQ", "PepsiCo", "Consumer Staples"),
    _share("PM", "NYSE", "Philip Morris International", "Consumer Staples", TOBACCO),
    _share("LMT", "NYSE", "Lockheed Martin", "Industrials", WEAPONS),
    # United Kingdom
    _share("AZN", "LSE", "AstraZeneca", "Health Care"),
    _share("SHEL", "LSE", "Shell", "Energy", FOSSIL),
    _share("HSBA", "LSE", "HSBC Holdings", "Financials"),
    _share("ULVR", "LSE", "Unilever", "Consumer Staples"),
    _share("GSK", "LSE", "GSK", "Health Care"),
    _share("BP", "LSE", "BP", "Energy", FOSSIL),
    _share("BATS", "LSE", "British American Tobacco", "Consumer Staples", TOBACCO),
    # Canada
    _share("RY", "TSX", "Royal Bank of Canada", "Financials"),
    _share("SHOP", "TSX", "Shopify", "Technology"),
    _share("TD", "TSX", "Toronto-Dominion Bank", "Financials"),
    _share("ENB", "TSX", "Enbridge", "Energy", FOSSIL),
    _share("CNR", "TSX", "Canadian National Railway", "Industrials"),
    # Hong Kong
    _share("0700", "HKEX", "Tencent Holdings", "Communication"),
    _share("1299", "HKEX", "AIA Group", "Financials"),
    _share("9988", "HKEX", "Alibaba Group", "Consumer"),
    _share("0005", "HKEX", "HSBC Holdings (HK listing)", "Financials"),
    _share("0941", "HKEX", "China Mobile", "Communication"),
    # Singapore
    _share("D05", "SGX", "DBS Group", "Financials"),
    _share("O39", "SGX", "OCBC", "Financials"),
    _share("U11", "SGX", "United Overseas Bank", "Financials"),
    # New Zealand
    _share("FPH", "NZX", "Fisher & Paykel Healthcare", "Health Care"),
    _share("AIA", "NZX", "Auckland International Airport", "Industrials"),
    _share("MEL", "NZX", "Meridian Energy", "Utilities"),
    _share("SPK", "NZX", "Spark New Zealand", "Communication"),
]

# Higher-risk growth companies for speculative goals only, never in an ordinary goal's mix.
# Roughly most established first within each theme. Shares above can also carry themes.
_AI, _CYBER, _CLEAN, _BIO = Theme.AI, Theme.CYBERSECURITY, Theme.CLEAN_ENERGY, Theme.BIOTECH
SPECULATIVE_SHARES: list[Instrument] = [
    # AI, robotics and semiconductors
    _share("AMD", "NASDAQ", "Advanced Micro Devices", "Technology", themes=(_AI,)),
    _share("TSM", "NYSE", "Taiwan Semiconductor Manufacturing (ADR)", "Technology",
           themes=(_AI,)),
    _share("ASML", "NASDAQ", "ASML Holding (ADR)", "Technology", themes=(_AI,)),
    _share("AVGO", "NASDAQ", "Broadcom", "Technology", themes=(_AI,)),
    _share("PLTR", "NASDAQ", "Palantir Technologies", "Technology", WEAPONS,  # defence work
           themes=(_AI,)),
    _share("ARM", "NASDAQ", "Arm Holdings (ADR)", "Technology", themes=(_AI,)),
    _share("SNOW", "NYSE", "Snowflake", "Technology", themes=(_AI,)),
    _share("NXT", "ASX", "NEXTDC", "Technology", themes=(_AI,)),  # data centres
    _share("WTC", "ASX", "WiseTech Global", "Technology", themes=(_AI,)),
    _share("MAQ", "ASX", "Macquarie Technology Group", "Technology", themes=(_AI,)),
    _share("BRN", "ASX", "BrainChip Holdings", "Technology", themes=(_AI,)),
    # Cybersecurity
    _share("CRWD", "NASDAQ", "CrowdStrike", "Technology", themes=(_CYBER,)),
    _share("PANW", "NASDAQ", "Palo Alto Networks", "Technology", themes=(_CYBER,)),
    _share("FTNT", "NASDAQ", "Fortinet", "Technology", themes=(_CYBER,)),
    _share("ZS", "NASDAQ", "Zscaler", "Technology", themes=(_CYBER,)),
    _share("NET", "NYSE", "Cloudflare", "Technology", themes=(_CYBER,)),
    # Clean energy and electric vehicles
    _share("TSLA", "NASDAQ", "Tesla", "Consumer", themes=(_CLEAN,)),
    _share("FSLR", "NASDAQ", "First Solar", "Energy", themes=(_CLEAN,)),
    _share("ENPH", "NASDAQ", "Enphase Energy", "Energy", themes=(_CLEAN,)),
    _share("PLS", "ASX", "Pilbara Minerals (lithium)", "Materials", themes=(_CLEAN,)),
    # Biotech
    _share("VRTX", "NASDAQ", "Vertex Pharmaceuticals", "Health Care", themes=(_BIO,)),
    _share("REGN", "NASDAQ", "Regeneron Pharmaceuticals", "Health Care", themes=(_BIO,)),
    _share("MRNA", "NASDAQ", "Moderna", "Health Care", themes=(_BIO,)),
    _share("CRSP", "NASDAQ", "CRISPR Therapeutics", "Health Care", themes=(_BIO,)),
    _share("TLX", "ASX", "Telix Pharmaceuticals", "Health Care", themes=(_BIO,)),
    _share("PME", "ASX", "Pro Medicus", "Health Care", themes=(_BIO, _AI)),
]

UNIVERSE: list[Instrument] = ETFS + SHARES + SPECULATIVE_SHARES
BY_KEY: dict[tuple[str, str], Instrument] = {i.key: i for i in UNIVERSE}


def lookup(ticker: str, exchange: str) -> Instrument | None:
    return BY_KEY.get((ticker.upper(), exchange.upper()))
