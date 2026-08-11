"""Product classification.

Placeholders only: the real classification taxonomies (departments, classes,
fines, classpaths) come from the official UniHack resources when available.
Values are free strings until then; no fake taxonomy is invented here.
"""

from pydantic import BaseModel, ConfigDict, Field


class Classification(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    department: str = ""
    # "class" is a Python keyword, so the attribute is class_ with alias "class".
    class_: str = Field(default="", alias="class")
    fine: str = ""
    classpath: str = ""
    product_type: str = ""
