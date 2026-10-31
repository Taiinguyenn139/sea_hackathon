"""Read and validate synthetic data without changing the running application.

The input loader never loads evaluator answers. Opt in to load_expectations or
the CLI --check-expectations flag to validate the separate reference answers.
"""

import argparse
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from simulators.identity_schemas import (
    Identity,
    IdentityFile,
    Journey,
    JourneyExpectations,
    Manifest,
    MemoryCheckpoint,
    Offer,
    OfferFile,
)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET_DIR = ROOT / "simulators/datasets/identity_v1"
DEFAULT_EXPECTATIONS_DIR = ROOT / "eval/oracle_fixtures/identity_v1"
PUBLIC_TOOLS = {
    "get_rate",
    "get_active_promotions",
    "get_card_offer",
    "calc_installment",
    "check_suitability",
    "get_disclosures",
}


def normalize_phone(value: str) -> str:
    """Canonical key for these fixtures, not a phone ownership check."""
    if not isinstance(value, str):
        raise TypeError("Phone identifiers must be strings to preserve the leading zero")
    compact = re.sub(r"[\s-]", "", value)
    if compact.startswith("+84"):
        compact = "0" + compact[3:]
    if not re.fullmatch(r"0[0-9]{9}", compact):
        raise ValueError("Expected a ten-digit fixture phone or its +84 representation")
    return compact


def _day(value: str | date) -> date:
    if type(value) is date:
        return value
    if not isinstance(value, str):
        raise TypeError("as_of must be a date or YYYY-MM-DD string")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("as_of must use YYYY-MM-DD")
    return parsed


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _unique(values, label):
    _require(len(values) == len(set(values)), f"Duplicate {label}")


def _read(path: Path, model):
    return model.model_validate_json(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class IdentityDataset:
    manifest: Manifest
    identities: dict[str, Identity]
    offers: dict[str, Offer]
    journeys: dict[str, Journey]

    def sessions_for(self, user_id):
        return list(self.journeys[normalize_phone(user_id)].sessions)

    def messages_for(self, user_id):
        return [message for session in self.sessions_for(user_id) for message in session.messages]

    def active_offers(self, product, channel, as_of, segment="mass"):
        day = _day(as_of)
        return [
            offer
            for offer in self.offers.values()
            if offer.product == product
            and channel in offer.channels
            and offer.segment == segment
            and offer.valid_from <= day <= offer.valid_until
            and offer.as_of <= day
        ]

    def summary(self):
        return {
            "dataset_id": self.manifest.dataset_id,
            "synthetic": True,
            "users": len(self.identities),
            "sessions": sum(len(journey.sessions) for journey in self.journeys.values()),
            "user_messages": sum(len(self.messages_for(uid)) for uid in self.identities),
            "banks": len({offer.bank_id for offer in self.offers.values()}),
            "offers": len(self.offers),
            "supported_runtime_channels": self.manifest.supported_runtime_channels,
            "simulated_only_channels": self.manifest.simulated_only_channels,
            "expectations_loaded": False,
        }


def load_dataset(directory=DEFAULT_DATASET_DIR) -> IdentityDataset:
    directory = Path(directory)
    manifest = _read(directory / "manifest.json", Manifest)
    identities_file = _read(directory / manifest.identity_file, IdentityFile)
    offers_file = _read(directory / manifest.offers_file, OfferFile)
    identities = identities_file.identities
    _unique([identity.user_id for identity in identities], "user_id")
    _unique([identity.bank_customer_id for identity in identities], "bank_customer_id")
    links = [(link.channel, link.external_id) for identity in identities for link in identity.channel_links]
    _unique(links, "channel identity link")
    _unique([bank.bank_id for bank in offers_file.banks], "bank_id")
    _unique([offer.offer_id for offer in offers_file.offers], "offer_id")
    bank_ids = {bank.bank_id for bank in offers_file.banks}
    for offer in offers_file.offers:
        _require(offer.bank_id in bank_ids, f"Unknown bank in offer {offer.offer_id}")
    _unique(manifest.journey_files, "journey file")
    _unique(manifest.supported_runtime_channels, "runtime channel")
    _unique(manifest.simulated_only_channels, "simulated channel")
    journeys = []
    for name in manifest.journey_files:
        path = (directory / name).resolve()
        _require(path.is_relative_to(directory.resolve()), "Journey path escapes dataset directory")
        journey = _read(path, Journey)
        _require(path.stem == journey.user_id, "Journey filename must match user_id")
        journeys.append(journey)
    _unique([journey.user_id for journey in journeys], "journey user_id")
    _unique([journey.scenario_id for journey in journeys], "scenario_id")
    identity_by_id = {identity.user_id: identity for identity in identities}
    _require(set(identity_by_id) == {journey.user_id for journey in journeys}, "Identity/journey users differ")
    timezone = ZoneInfo(manifest.business_timezone)
    session_ids, message_ids = [], []
    for journey in journeys:
        identity = identity_by_id[journey.user_id]
        channel_links = {link.channel: link.external_id for link in identity.channel_links}
        previous_end = None
        for session in journey.sessions:
            _require(session.user_id == journey.user_id, "Session belongs to another user")
            _require(channel_links.get(session.channel) == session.external_id, "Session channel identity mismatch")
            _require(identity.date_of_birth < session.as_of, "Birth date must precede session")
            _require(session.started_at.astimezone(timezone).date() == session.as_of, "Session start/as_of mismatch")
            if previous_end:
                _require(session.started_at > previous_end, "Sessions must be chronological and nonoverlapping")
            if session.verified_at:
                _require(session.verified_at.astimezone(timezone).date() == session.as_of, "Verification date mismatch")
            session_ids.append(session.session_id)
            previous_message = session.started_at
            for message in session.messages:
                _require(message.user_id == journey.user_id, "Message belongs to another user")
                _require(message.session_id == session.session_id, "Message session_id mismatch")
                _require(message.channel == session.channel, "Message channel mismatch")
                _require(message.at > previous_message, "Messages must follow session start in strict order")
                _require(message.at.astimezone(timezone).date() == session.as_of, "Message/as_of mismatch")
                if session.verified_at:
                    _require(session.verified_at <= message.at, "Message precedes session verification")
                message_ids.append(message.message_id)
                previous_message = message.at
            previous_end = previous_message
    _unique(session_ids, "session_id")
    _unique(message_ids, "message_id")
    counts = manifest.expected_counts
    _require(len(identities) == counts.users, "User count differs from manifest")
    _require(len(session_ids) == counts.sessions, "Session count differs from manifest")
    _require(len(message_ids) == counts.user_messages, "Message count differs from manifest")
    return IdentityDataset(
        manifest,
        identity_by_id,
        {offer.offer_id: offer for offer in offers_file.offers},
        {journey.user_id: journey for journey in journeys},
    )


def load_expectations(dataset: IdentityDataset, directory=DEFAULT_EXPECTATIONS_DIR):
    """Evaluator-only answers. Do not pass this return value to an agent."""
    directory = Path(directory)
    _require(
        {path.stem for path in directory.glob("*.json")} == set(dataset.identities),
        "Expectation files must match dataset users exactly",
    )
    expectations = {}
    reference_ids = []
    timezone = ZoneInfo(dataset.manifest.business_timezone)
    for user_id, journey in dataset.journeys.items():
        expected = _read(directory / f"{user_id}.json", JourneyExpectations)
        _require(
            expected.user_id == user_id and expected.scenario_id == journey.scenario_id,
            "Expectation scenario/user mismatch",
        )
        _require(len(expected.checkpoints) == len(journey.sessions), "Missing or extra memory checkpoints")
        messages = {message.message_id: message for message in dataset.messages_for(user_id)}
        sessions = {session.session_id: session for session in journey.sessions}
        previous_checkpoint: MemoryCheckpoint | None = None
        for session, checkpoint in zip(journey.sessions, expected.checkpoints, strict=True):
            _require(
                checkpoint.user_id == user_id and checkpoint.session_id == session.session_id,
                "Checkpoint belongs to another session/user",
            )
            _require(checkpoint.as_of == session.as_of, "Checkpoint date mismatch")
            allowed = session.identity_status == "verified"
            _require(
                checkpoint.cross_session_memory_allowed == allowed, "Memory permission does not match verification"
            )
            _require(allowed or not checkpoint.visible_memory_keys, "Unverified session cannot read customer memory")
            if not allowed:
                previous_memory = previous_checkpoint.customer_memory if previous_checkpoint else []
                _require(checkpoint.customer_memory == previous_memory, "Unverified session changed customer memory")
                previous_quotes = previous_checkpoint.quotes if previous_checkpoint else []
                signature = lambda quote: (quote.offer_id, quote.source_message_id, quote.quoted_at)
                _require(
                    [signature(q) for q in checkpoint.quotes] == [signature(q) for q in previous_quotes],
                    "Unverified session changed saved customer quotes",
                )
            checkpoint_end = session.messages[-1].at

            _unique(
                [
                    (fact.key, str(fact.value), fact.source_message_id, fact.status)
                    for fact in checkpoint.customer_memory
                ],
                "memory fact",
            )
            _unique([fact.key for fact in checkpoint.customer_memory if fact.status == "active"], "active memory key")
            for fact in checkpoint.customer_memory:
                _memory_source(fact.source_message_id, messages, sessions, checkpoint_end)
            visible = {fact.key for fact in checkpoint.customer_memory if fact.status == "active"}
            _require(set(checkpoint.visible_memory_keys) <= visible, "Visible memory key has no active fact")
            for quote in checkpoint.quotes:
                message = _memory_source(quote.source_message_id, messages, sessions, checkpoint_end)
                offer = dataset.offers.get(quote.offer_id)
                if offer is None:
                    raise ValueError("Unknown quote offer_id")
                _require(quote.quoted_at == sessions[message.session_id].as_of, "Quote source date mismatch")
                _valid_offer(offer, journey, message.channel, quote.quoted_at)
                _require(quote.quoted_at <= checkpoint.as_of, "Quote is from the future")
                status = "expired" if offer.valid_until < checkpoint.as_of else "active"
                _require(quote.status == status, "Quote expiry status is incorrect")
            _require(
                [response.in_reply_to for response in checkpoint.responses]
                == [message.message_id for message in session.messages],
                "Each input needs one ordered reference reply",
            )
            for index, response in enumerate(checkpoint.responses):
                message = session.messages[index]
                _require(
                    response.user_id == user_id and response.session_id == session.session_id,
                    "Reference belongs to another user/session",
                )
                _require(response.channel == session.channel, "Reference channel mismatch")
                _require(response.at > message.at, "Reference must follow its input")
                _require(response.at.astimezone(timezone).date() == session.as_of, "Reference date mismatch")
                if index + 1 < len(session.messages):
                    _require(response.at < session.messages[index + 1].at, "Reference reveals a future turn")
                if not allowed:
                    _require(set(response.suggested_tools) <= PUBLIC_TOOLS, "Unverified session suggests private tool")
                for offer_id in response.offer_ids:
                    _require(offer_id in dataset.offers, "Unknown response offer_id")
                    _valid_offer(
                        dataset.offers[offer_id],
                        journey,
                        session.channel,
                        session.as_of,
                        segment=journey.offer_segment if allowed else "mass",
                    )
                reference_ids.append(response.message_id)
            previous_checkpoint = checkpoint
        expectations[user_id] = expected
    _unique(reference_ids, "assistant message_id")
    user_ids = {message.message_id for uid in dataset.identities for message in dataset.messages_for(uid)}
    _require(not user_ids.intersection(reference_ids), "Assistant and user message IDs collide")
    _require(
        len(reference_ids) == dataset.manifest.expected_counts.assistant_references,
        "Assistant reference count differs from manifest",
    )
    return expectations


def _memory_source(message_id, messages, sessions, checkpoint_end):
    message = messages.get(message_id)
    _require(message is not None, "Unknown or foreign memory source_message_id")
    _require(message.at <= checkpoint_end, "Memory source comes from a future message")
    _require(
        sessions[message.session_id].identity_status == "verified", "Unverified session cannot write customer memory"
    )
    return message


def _valid_offer(offer, journey, channel, as_of, *, segment=None):
    _require(
        offer.product == journey.product and offer.segment == (segment or journey.offer_segment),
        "Offer product/segment mismatch",
    )
    _require(channel in offer.channels, "Offer channel mismatch")
    _require(
        offer.valid_from <= as_of <= offer.valid_until and offer.as_of <= as_of,
        "Offer is unavailable, future or expired on the business date",
    )


def reference_transcript(dataset, expectations, user_id):
    """Example transcript, not measured model output or an agent prompt."""
    user_id = normalize_phone(user_id)
    expected = expectations[user_id]
    replies = {reply.in_reply_to: reply for checkpoint in expected.checkpoints for reply in checkpoint.responses}
    result = []
    for message in dataset.messages_for(user_id):
        result.append(message.model_dump(mode="json"))
        response = replies[message.message_id]
        result.append({**response.model_dump(mode="json"), "reference": True})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR)
    parser.add_argument("--expectations-dir", type=Path, default=DEFAULT_EXPECTATIONS_DIR)
    parser.add_argument("--check-expectations", action="store_true")
    parser.add_argument("--user-id")
    parser.add_argument(
        "--transcript", action="store_true", help="Show evaluator reference replies, never an agent input"
    )
    args = parser.parse_args(argv)
    if args.transcript and not (args.check_expectations and args.user_id):
        parser.error("--transcript requires --check-expectations and --user-id")
    try:
        dataset = load_dataset(args.dataset_dir)
        expectations = load_expectations(dataset, args.expectations_dir) if args.check_expectations else None
        if args.user_id:
            user_id = normalize_phone(args.user_id)
            if args.transcript:
                output = {
                    "user_id": user_id,
                    "synthetic": True,
                    "record_type": "reference_transcript",
                    "messages": reference_transcript(dataset, expectations, user_id),
                }
            else:
                output = {
                    "user_id": user_id,
                    "synthetic": True,
                    "record_type": "user_replay",
                    "sessions": [session.model_dump(mode="json") for session in dataset.sessions_for(user_id)],
                }
        else:
            output = dataset.summary()
            if expectations is not None:
                output.update(
                    expectations_loaded=True,
                    memory_checkpoints=sum(len(item.checkpoints) for item in expectations.values()),
                    assistant_references=sum(
                        len(cp.responses) for item in expectations.values() for cp in item.checkpoints
                    ),
                )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
