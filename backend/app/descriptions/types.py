"""Typed output model for LLM-generated product descriptions.

Maps 1:1 onto the internal domain ``Descriptions`` (app.core.domain), whose
fields align with the official UniHack delivery columns (MOBILE_DESC,
INVOICE_DESC, SHORT_DESC, LONG_DESC1, RETAIL_DESC, MARKETING_DESCRIPTION,
ITEM_FEATURES_1..20, With, Application, Includes, Product Name).

The JSON key for the ``with`` field uses the alias ``with`` (a Python
keyword), while the Python attribute stays ``with_`` to match the domain
model. All fields are optional with empty defaults: the generator may
legitimately produce nothing for a variant rather than inventing content.
"""

from pydantic import BaseModel, ConfigDict, Field


class GeneratedDescriptions(BaseModel):
    """All commerce-ready description variants for one product (LLM output)."""

    model_config = ConfigDict(populate_by_name=True)

    product_title: str = ""
    short_description: str = ""
    mobile_description: str = ""
    invoice_description: str = ""
    long_description: str = ""
    retail_description: str = ""
    marketing_description: str = ""
    item_features: list[str] = Field(default_factory=list)
    # Delivery column "With"; JSON key is "with", Python attribute is "with_".
    with_: str = Field(default="", alias="with", serialization_alias="with")
    application: str = ""
    includes: str = ""
    product_name: str = ""
