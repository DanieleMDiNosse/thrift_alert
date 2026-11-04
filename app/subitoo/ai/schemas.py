"""Strict untrusted-model output validation and source-reference integrity."""

from typing import Annotated, Literal
import re
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

Text = Annotated[StrictStr, Field(max_length=6000)]
Label = Annotated[StrictStr, Field(min_length=1, max_length=200)]
Confidence = Annotated[float, Field(ge=0, le=1, strict=True, allow_inf_nan=False)]


def canonical_category(value):
    # Explicit policy synonyms, never fragrance diffusers or speaker accessories.
    return {
        "speaker": "audio",
        "speakers": "audio",
        "passive_bookshelf_speakers": "audio",
    }.get(value, value)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Identity(StrictModel):
    category: Label
    manufacturer: Label | None
    name: Label | None
    model: Label | None
    generation: Label | None
    variant: Label | None
    confidence: Confidence

    @field_validator("category")
    @classmethod
    def normalize_category(cls, value):
        value = value.strip().casefold().replace(" ", "_")
        if not re.fullmatch(r"[a-z_]{1,40}", value):
            raise ValueError("Category must be a short canonical identifier")
        return canonical_category(value)

    @field_validator("manufacturer", "name", "model", "generation", "variant")
    @classmethod
    def unknown_is_null(cls, v):
        if v and v.strip().casefold() in {
            "unknown",
            "unidentified",
            "n/a",
            "?",
            "not specified",
        }:
            return None
        return (v.strip() or None) if v else v


class Criterion(StrictModel):
    score: Annotated[float, Field(ge=0, le=100, strict=True, allow_inf_nan=False)]
    source_ids: Annotated[list[Label], Field(min_length=1, max_length=20)]


class SourceAssessment(StrictModel):
    source_type: Literal[
        "official_specification",
        "professional_review",
        "independent_comparison",
        "user_experience",
        "unverified_opinion",
        "promotional",
    ]
    reliability: Literal["high", "moderate", "low", "unassessed"]
    rationale: Text


class Research(StrictModel):
    criteria: Annotated[dict[Label, Criterion], Field(max_length=20)]
    strengths: Annotated[list[Text], Field(max_length=20)]
    weaknesses: Annotated[list[Text], Field(max_length=20)]
    known_issues: Annotated[list[Text], Field(max_length=20)]
    specifications: Annotated[dict[Label, Text], Field(max_length=60)]
    summary: Text
    confidence: Confidence
    source_assessments: Annotated[
        dict[Label, SourceAssessment], Field(max_length=20)
    ] = {}
    professional_summary: Text = ""
    user_summary: Text = ""


class Chat(StrictModel):
    message: Annotated[StrictStr, Field(min_length=1, max_length=16000)]
    source_ids: Annotated[list[Label], Field(max_length=100)]


class ListingFacts(StrictModel):
    condition: Literal["new", "like_new", "good", "fair", "poor", "unknown"] = "unknown"
    shipping_cost: (
        Annotated[float, Field(ge=0, strict=True, allow_inf_nan=False)] | None
    ) = None
    fees: Annotated[float, Field(ge=0, strict=True, allow_inf_nan=False)] | None = None
    age_years: (
        Annotated[float, Field(ge=0, le=200, strict=True, allow_inf_nan=False)] | None
    ) = None
    warranty: Text | None = None
    accessories: Annotated[list[Label], Field(max_length=30)] = []


MODELS = {"identity": Identity, "research": Research, "chat": Chat}


def validate_identity(value):
    return Identity.model_validate(value).model_dump()


def validate_research(value, source_ids):
    result = Research.model_validate(value).model_dump()
    cited = {
        sid
        for criterion in result["criteria"].values()
        for sid in criterion["source_ids"]
    }
    if not cited <= set(source_ids):
        raise ValueError("Research references unavailable evidence")
    if not set(result["source_assessments"]) <= set(source_ids):
        raise ValueError("Assessment references unavailable evidence")
    if (
        result["criteria"]
        or result["specifications"]
        or result["strengths"]
        or result["known_issues"]
    ) and not source_ids:
        raise ValueError("Research requires retrieved evidence")
    return result


def validate_chat(value, source_ids):
    result = Chat.model_validate(value).model_dump()
    if re.search(r"https?://|www\.", result["message"], re.I):
        raise ValueError("Use structured source IDs, not generated URLs")
    if not set(result["source_ids"]) <= set(source_ids):
        raise ValueError("Chat references unavailable evidence")
    return result
