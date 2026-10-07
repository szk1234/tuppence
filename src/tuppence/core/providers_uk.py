"""UK banks, card issuers and building societies offered in the account step."""

from __future__ import annotations

from dataclasses import dataclass

from tuppence.core.errors import InputError

_ALL = ("current", "savings", "credit_card")
_CARD = ("credit_card",)


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    kinds: tuple[str, ...]


PROVIDERS: list[Provider] = [
    Provider("monzo", "Monzo", _ALL),
    Provider("starling", "Starling Bank", _ALL),
    Provider("revolut", "Revolut", _ALL),
    Provider("chase", "Chase", _ALL),
    Provider("hsbc", "HSBC", _ALL),
    Provider("first_direct", "first direct", _ALL),
    Provider("barclays", "Barclays", _ALL),
    Provider("lloyds", "Lloyds Bank", _ALL),
    Provider("halifax", "Halifax", _ALL),
    Provider("bank_of_scotland", "Bank of Scotland", _ALL),
    Provider("natwest", "NatWest", _ALL),
    Provider("rbs", "Royal Bank of Scotland", _ALL),
    Provider("ulster_bank", "Ulster Bank", _ALL),
    Provider("santander", "Santander", _ALL),
    Provider("nationwide", "Nationwide", _ALL),
    Provider("tsb", "TSB", _ALL),
    Provider("coop_bank", "Co-operative Bank", _ALL),
    Provider("metro_bank", "Metro Bank", _ALL),
    Provider("virgin_money", "Virgin Money", _ALL),
    Provider("kroo", "Kroo", _ALL),
    Provider("atom", "Atom bank", _ALL),
    Provider("zopa", "Zopa", _ALL),
    Provider("amex", "American Express", _CARD),
    Provider("barclaycard", "Barclaycard", _CARD),
    Provider("capital_one", "Capital One", _CARD),
    Provider("mbna", "MBNA", _CARD),
    Provider("tesco_bank", "Tesco Bank", _ALL),
    Provider("sainsburys_bank", "Sainsbury's Bank", _ALL),
    Provider("aqua", "Aqua", _CARD),
    Provider("vanquis", "Vanquis", _CARD),
    Provider("marbles", "Marbles", _CARD),
    Provider("fluid", "Fluid", _CARD),
    Provider("jaja", "Jaja", _CARD),
    Provider("other", "Other", ("current", "savings", "credit_card", "cash_wallet")),
]

_BY_ID = {p.id: p for p in PROVIDERS}


def provider_name(provider_id: str) -> str:
    provider = _BY_ID.get(provider_id)
    if provider is None:
        raise InputError("Unknown provider.")
    return provider.name
