"""Commerce-ready descriptions and content fields.

No character limits or formulas are assumed: the exact UniHack requirements
for each description variant (lengths, templates) are not known yet and plug
in during generation stages later. All fields default to empty and may stay
empty until the future description-generation stage runs.

Field names map onto the actual UniHack Delivery Format columns (Step 6A):
product_title / short_description / mobile_description / invoice_description
/ long_description plus the delivery content fields RETAIL_DESC,
MARKETING_DESCRIPTION, ITEM_FEATURES_1..20, With, Application, Includes and
Product Name.
"""

from pydantic import BaseModel, Field


class Descriptions(BaseModel):
    product_title: str = ""
    short_description: str = ""
    mobile_description: str = ""
    invoice_description: str = ""
    long_description: str = ""
    # Delivery column RETAIL_DESC.
    retail_description: str = ""
    # Delivery column MARKETING_DESCRIPTION.
    marketing_description: str = ""
    # Delivery columns ITEM_FEATURES_1..20 (in order; empty entries allowed).
    # The delivery mapper truncates to the 20 delivery slots; the generic
    # model itself does not cap the list so the delivery format owns limits.
    item_features: list[str] = Field(default_factory=list)
    # Delivery column "With".
    with_: str = ""
    # Delivery column "Application".
    application: str = ""
    # Delivery column "Includes".
    includes: str = ""
    # Delivery column "Product Name" (e.g. "Dishwasher").
    product_name: str = ""
