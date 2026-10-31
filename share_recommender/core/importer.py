"""Read broker CSV exports into holdings (one per purchase), for the person to review.

Three layouts are understood:

- **CommSec**: a trade confirmations or transactions export where a "Details" column reads
  like ``B 100 VAS @ 95.500000`` and the debit includes brokerage.
- **Columns**: any export with separate columns for date, code, quantity and price, such
  as CMC Markets' transaction history. Columns are matched by common header names, and
  the person can correct the match before importing.
- **Snapshot**: a holdings or profit-and-loss export with one row per stock and no dates,
  such as CMC Markets' "ProfitLoss" export (Code, Units Held, Net Avg Price AUD, FX Rate).
  The total cost (Cost AUD) is used as the cost, so the buy price is cost / units; the
  average price is only a fallback when there is no cost column. It is meant to be imported
  after the transaction history: for each stock it adds only the units the existing holdings
  don't already cover, as one parcel dated at an "as at" date (the earliest date the
  transaction history reaches), priced so the total cost matches.

The exact column names brokers use change from time to time, so detection is a best guess
and the preview always shows what will be imported. Only purchases become holdings; sales
are listed so the person can adjust their parcels by hand.
"""

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from uuid import UUID

from pydantic import ValidationError

from share_recommender.core.exchanges import EXCHANGES
from share_recommender.core.models import Holding, HoldingIn
from share_recommender.core.universe import us_exchange


class Layout(StrEnum):
    COMMSEC = "commsec"
    COLUMNS = "columns"
    SNAPSHOT = "snapshot"


class Broker(StrEnum):
    COMMSEC = "CommSec"
    CMC = "CMC Markets"
    OTHER = "Other"


FIELDS = ["date", "side", "ticker", "exchange", "units", "price", "fees", "value", "details",
          "fx", "cost"]
REQUIRED = {Layout.COMMSEC: ["date", "details"],
            Layout.COLUMNS: ["date", "ticker", "units", "price"],
            Layout.SNAPSHOT: ["ticker", "units", "cost"]}  # or "price" in place of "cost"
NO_COST = ("No cost recorded (for example, shares received in a demerger). Enter a cost per "
           "unit in the Price column to import it.")

# Header names seen in broker exports, lower case, most specific first.
SYNONYMS: dict[str, list[str]] = {
    "date": ["trade date", "transaction date", "order date", "date", "execution date",
             "settlement date", "confirmation date"],
    "side": ["buy/sell", "side", "direction", "transaction type", "order type", "action",
             "type", "b/s"],
    "ticker": ["asx code", "security code", "instrument code", "stock code", "code", "symbol",
               "ticker", "security", "instrument", "stock"],
    "exchange": ["exchange", "market", "venue"],
    "units": ["units held", "quantity held", "quantity", "units", "qty", "volume", "shares",
              "filled quantity", "no. of units", "holding"],
    "price": ["net avg price aud", "avg price aud", "average price aud", "net avg price",
              "average price", "avg price", "trade price", "executed price", "unit price",
              "price", "price ($)", "fill price", "average cost", "avg cost"],
    "fees": ["brokerage", "brokerage (inc gst)", "brokerage fee", "commission", "fees", "fee"],
    "value": ["consideration", "total value", "net value", "gross value", "trade value", "value",
              "amount", "total", "debit($)", "debit"],
    "details": ["details", "description", "narrative"],
    "fx": ["fx rate", "exchange rate", "fx"],
    "cost": ["cost aud", "cost base", "cost", "book cost", "total cost"],
}

_EXCHANGE_NAMES = {
    "ASX": "ASX", "AU": "ASX", "AUS": "ASX", "XASX": "ASX", "AX": "ASX", "CXA": "ASX",
    "NYSE": "NYSE", "NYQ": "NYSE", "ARCA": "NYSE", "NYSEARCA": "NYSE",
    "US": "US",  # resolved to NASDAQ or NYSE by ticker
    "NASDAQ": "NASDAQ", "NMS": "NASDAQ", "NAS": "NASDAQ", "NSDQ": "NASDAQ",
    "LSE": "LSE", "LON": "LSE", "L": "LSE", "TSX": "TSX", "TO": "TSX", "SGX": "SGX", "SI": "SGX",
    "HKEX": "HKEX", "HK": "HKEX", "NZX": "NZX", "NZ": "NZX",
}
_COMMSEC_DETAILS = re.compile(r"^\s*([BS])\s+([\d,]+(?:\.\d+)?)\s+([A-Z0-9.]+)\s+@\s+([\d.,]+)",
                              re.IGNORECASE)
_DATE_FORMATS = ["%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%d %b %Y", "%d-%b-%Y",
                 "%d-%b-%y", "%d %B %Y", "%Y/%m/%d", "%d.%m.%Y"]


@dataclass
class Mapping:
    layout: Layout
    columns: dict[str, str | None]  # field -> CSV column
    broker: Broker = Broker.OTHER

    def missing(self) -> list[str]:
        missing = [f for f in REQUIRED[self.layout] if not self.columns.get(f)]
        if self.layout is Layout.SNAPSHOT and "cost" in missing and self.columns.get("price"):
            missing.remove("cost")  # an average price will do
        return missing

    def in_aud(self, name: str) -> bool:
        """Snapshot amounts labelled AUD are converted to the exchange's currency."""
        return "aud" in (self.columns.get(name) or "").lower()

    @property
    def price_in_aud(self) -> bool:
        return self.in_aud("cost" if self.columns.get("cost") else "price")


@dataclass
class ImportRow:
    line: int                    # row number in the file, counting the header as 1
    side: str                    # "buy", "sell", "skip" or "covered"
    holding: HoldingIn | None
    problem: str | None = None
    duplicate: bool = False
    raw: dict[str, str] = field(default_factory=dict)
    note: str | None = None
    # A purchase missing only its price: every other HoldingIn field, for the person to
    # complete in the preview.
    partial: dict | None = None

    @property
    def importable(self) -> bool:
        return self.side == "buy" and self.holding is not None and self.problem is None


# Reading -------------------------------------------------------------------------

def read_csv(text: str) -> tuple[list[str], list[dict[str, str]], int]:
    """Header, rows and the header's line number (1-based).

    Skips title lines above the header, which some exports include."""
    text = text.lstrip("﻿")
    lines = text.splitlines()
    known = {s for names in SYNONYMS.values() for s in names}
    start = 0
    for i, line in enumerate(lines[:20]):
        cells = {c.strip().strip('"').lower() for c in next(csv.reader([line]), [])}
        if len(cells & known) >= 2:
            start = i
            break
    reader = csv.DictReader(io.StringIO("\n".join(lines[start:])))
    header = [h.strip() for h in (reader.fieldnames or [])]
    rows = []
    for r in reader:
        row = {(k or "").strip(): (v or "").strip() for k, v in r.items() if k is not None}
        if any(row.values()):
            rows.append(row)
    return header, rows, start + 1


def detect(header: list[str]) -> Mapping:
    lower = {h.lower(): h for h in header}

    def find(name: str) -> str | None:
        return next((lower[s] for s in SYNONYMS[name] if s in lower), None)

    columns = {f: find(f) for f in FIELDS}
    if columns["details"] and not (columns["units"] and columns["price"]):
        return Mapping(Layout.COMMSEC, columns, Broker.COMMSEC)
    if not columns["date"] and columns["units"] and columns["price"]:
        return Mapping(Layout.SNAPSHOT, columns,
                       Broker.CMC if "units held" in lower else Broker.OTHER)
    return Mapping(Layout.COLUMNS, columns, Broker.OTHER)


# Parsing values -----------------------------------------------------------------

def parse_number(text: str | None) -> Decimal | None:
    if text is None:
        return None
    t = text.strip().replace("$", "").replace(",", "").replace("AUD", "").replace("USD", "")
    negative = t.startswith("(") and t.endswith(")")
    t = t.strip("()").strip()
    if not t or t in {"-", "--"}:
        return None
    try:
        n = Decimal(t)
    except InvalidOperation:
        return None
    return -n if negative else n


def parse_date(text: str | None) -> date | None:
    if not text:
        return None
    candidates = [text.strip(), text.strip().split("T")[0], text.strip().split(" ")[0]]
    for t in candidates:
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(t, fmt).date()
            except ValueError:
                continue
    return None


def parse_ticker(text: str, exchange_text: str | None, default_exchange: str
                 ) -> tuple[str, str | None]:
    """'VAS', 'VAS.AX', 'ASX:VAS' or 'AAPL.US' -> (ticker, exchange). Exchange None if unknown."""
    t = text.strip().upper()
    exchange = None
    if ":" in t:
        a, b = t.split(":", 1)
        if a in _EXCHANGE_NAMES:
            exchange, t = _EXCHANGE_NAMES[a], b
        elif b in _EXCHANGE_NAMES:
            t, exchange = a, _EXCHANGE_NAMES[b]
    elif "." in t:
        a, b = t.rsplit(".", 1)
        if b in _EXCHANGE_NAMES:
            t, exchange = a, _EXCHANGE_NAMES[b]
    if exchange_text and exchange_text.strip():
        # An exchange the file names but we don't know is flagged, not guessed.
        exchange = _EXCHANGE_NAMES.get(exchange_text.strip().upper(), "?")
    exchange = exchange or default_exchange
    if exchange == "US":
        exchange = us_exchange(t)
    return t, exchange if exchange in EXCHANGES else None


def _side(text: str | None, units: Decimal | None) -> str:
    t = (text or "").strip().lower()
    if re.match(r"^(b|buy|bought|purchase|buy to open)\b", t):
        return "buy"
    if re.match(r"^(s|sell|sold|sale)\b", t):
        return "sell"
    if t:
        return "skip"  # dividends, deposits, fees and so on
    return "sell" if units is not None and units < 0 else "buy"


# Turning rows into holdings -----------------------------------------------------

def parse_rows(rows: list[dict[str, str]], mapping: Mapping, *, default_exchange: str = "ASX",
               platform: str | None = None, category_id: UUID | None = None,
               existing: list[Holding] = (), header_line: int = 1,
               as_at: date | None = None) -> list[ImportRow]:
    """Turn CSV rows into holdings to review. ``as_at`` dates a snapshot's holdings."""
    col = mapping.columns

    def get(row: dict[str, str], name: str) -> str | None:
        c = col.get(name)
        return row.get(c) if c else None

    if mapping.layout is Layout.SNAPSHOT:
        return _snapshot_rows(rows, get, mapping, default_exchange, platform, category_id,
                              existing, header_line, as_at)

    seen = {_key(h.ticker, h.exchange, h.buy_date, h.units, h.buy_price) for h in existing}
    out = []
    for i, row in enumerate(rows, start=header_line + 1):
        if mapping.layout is Layout.COMMSEC:
            out.append(_commsec_row(i, row, get, default_exchange, platform, category_id))
        else:
            out.append(_columns_row(i, row, get, default_exchange, platform, category_id))
        r = out[-1]
        if r.holding is not None and r.side == "buy":
            k = _key(r.holding.ticker, r.holding.exchange, r.holding.buy_date, r.holding.units,
                     r.holding.buy_price)
            r.duplicate = k in seen
            seen.add(k)
    return out


def _key(ticker, exchange, day, units, price) -> tuple:
    return (ticker, exchange, day, Decimal(units).normalize(),
            Decimal(price).quantize(Decimal("0.0001")))


def _holding(line, raw, side, *, ticker, exchange, units, price, fees, day, platform,
             category_id) -> ImportRow:
    if day is None:
        return ImportRow(line, side, None, "Couldn't read the date", raw=raw)
    if exchange is None:
        return ImportRow(line, side, None, "Unknown exchange", raw=raw)
    if side == "sell":
        return ImportRow(line, side, None, "Sale: not imported. Reduce or delete the parcel "
                         "you sold on the Holdings page.", raw=raw)
    if price is None or price <= 0:
        partial = {"ticker": ticker, "exchange": exchange, "units": abs(units), "buy_date": day,
                   "fees": max(fees or Decimal(0), Decimal(0)), "platform": platform,
                   "category_id": category_id}
        return ImportRow(line, side, None, NO_COST, raw=raw, partial=partial)
    try:
        holding = HoldingIn(ticker=ticker, exchange=exchange, units=abs(units), buy_price=price,
                            buy_date=day, fees=max(fees or Decimal(0), Decimal(0)),
                            platform=platform, category_id=category_id)
    except ValidationError as exc:
        err = exc.errors()[0]
        field_name = " ".join(str(p) for p in err["loc"]).replace("_", " ")
        return ImportRow(line, side, None, f"{field_name.capitalize()}: {err['msg']}", raw=raw)
    return ImportRow(line, side, holding, raw=raw)


def _commsec_row(line, row, get, default_exchange, platform, category_id) -> ImportRow:
    details = get(row, "details") or ""
    m = _COMMSEC_DETAILS.match(details)
    if not m:
        return ImportRow(line, "skip", None, "Not a trade", raw=row)
    side = "buy" if m.group(1).upper() == "B" else "sell"
    units, price = parse_number(m.group(2)), parse_number(m.group(4))
    ticker, exchange = parse_ticker(m.group(3), get(row, "exchange"), default_exchange)
    # The debit is units x price plus brokerage, so the difference is the fee.
    total = parse_number(get(row, "value"))
    fees = parse_number(get(row, "fees"))
    if fees is None and total is not None and side == "buy":
        fees = (abs(total) - units * price).quantize(Decimal("0.01"))
    return _holding(line, row, side, ticker=ticker, exchange=exchange, units=units, price=price,
                    fees=fees, day=parse_date(get(row, "date")), platform=platform,
                    category_id=category_id)


def _columns_row(line, row, get, default_exchange, platform, category_id) -> ImportRow:
    units, price = parse_number(get(row, "units")), parse_number(get(row, "price"))
    side = _side(get(row, "side"), units)
    if side == "skip":
        return ImportRow(line, "skip", None, f"Not a trade ({get(row, 'side')})", raw=row)
    code = get(row, "ticker")
    if not code or units is None or price is None:
        return ImportRow(line, "skip", None, "Missing code, quantity or price", raw=row)
    ticker, exchange = parse_ticker(code, get(row, "exchange"), default_exchange)
    fees = parse_number(get(row, "fees"))
    total = parse_number(get(row, "value"))
    if fees is None and total is not None:
        diff = abs(total) - abs(units) * price
        # Only trust a small positive difference as brokerage, not rounding or GST quirks.
        fees = diff.quantize(Decimal("0.01")) if 0 < diff < abs(total) * Decimal("0.05") \
            else Decimal(0)
    return _holding(line, row, side, ticker=ticker, exchange=exchange, units=units, price=price,
                    fees=fees, day=parse_date(get(row, "date")), platform=platform,
                    category_id=category_id)


def _snapshot_rows(rows, get, mapping, default_exchange, platform, category_id, existing,
                   header_line, as_at) -> list[ImportRow]:
    held: dict[tuple[str, str], tuple[Decimal, Decimal]] = {}
    for h in existing:
        units, cost = held.get((h.ticker, h.exchange), (Decimal(0), Decimal(0)))
        held[(h.ticker, h.exchange)] = (units + h.units, cost + h.cost_base)

    out = []
    for line, row in enumerate(rows, start=header_line + 1):
        code, units = get(row, "ticker"), parse_number(get(row, "units"))
        if not code or not units:
            out.append(ImportRow(line, "skip", None, "Missing code or units", raw=row))
            continue
        ticker, exchange = parse_ticker(code, get(row, "exchange"), default_exchange)
        if exchange is None:
            out.append(ImportRow(line, "buy", None, "Unknown exchange", raw=row))
            continue
        # The total cost is exact; the average price is rounded, so it's only a fallback.
        cost = parse_number(get(row, "cost"))
        price = cost / units if cost is not None else parse_number(get(row, "price"))
        currency = EXCHANGES[exchange].currency
        if price and mapping.price_in_aud and currency != "AUD":
            fx = parse_number(get(row, "fx"))  # units of that currency per 1 AUD
            if not fx:
                out.append(ImportRow(line, "buy", None, f"No FX rate to convert the AUD cost "
                                     f"to {currency}", raw=row))
                continue
            price = price * fx

        have, have_cost = held.get((ticker, exchange), (Decimal(0), Decimal(0)))
        if have > units:
            out.append(ImportRow(
                line, "buy", None, f"Your holdings have {have - units:g} more {ticker} than this "
                "file shows. If you sold some, reduce that parcel on the Holdings page.", raw=row))
            continue
        if have == units:
            out.append(ImportRow(line, "covered", None, raw=row,
                                 note="Already covered by your holdings"))
            continue
        remaining, note = units - have, None
        if have and price:
            # Price the rest so the total cost matches the broker's average.
            rest = (units * price - have_cost) / remaining
            if rest > 0:
                price = rest
                note = f"Adds the {remaining:g} of {units:g} units your holdings don't cover"
            else:
                note = (f"Adds the {remaining:g} of {units:g} units your holdings don't cover, "
                        "at the broker's average price")
        if as_at is None:
            out.append(ImportRow(line, "buy", None, "Choose a date for these holdings", raw=row))
            continue
        r = _holding(line, row, "buy", ticker=ticker, exchange=exchange, units=remaining,
                     price=price.quantize(Decimal("0.000001")) if price else price,
                     fees=Decimal(0), day=as_at, platform=platform, category_id=category_id)
        r.note = note
        out.append(r)
    return out


def guess_broker(mapping: Mapping, filename: str | None = None) -> Broker:
    name = (filename or "").lower()
    if "commsec" in name or mapping.layout is Layout.COMMSEC:
        return Broker.COMMSEC
    if "cmc" in name or mapping.broker is Broker.CMC:
        return Broker.CMC
    return Broker.OTHER
