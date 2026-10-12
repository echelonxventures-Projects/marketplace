"""U03 — Product & Service Knowledge.

Canonical concepts owned: product, product_variant, product_family, taxonomy,
attribute_schema, brand, media_asset, external_identifier mapping.

REQ-CAT-001..010. Enforces the catalog hierarchy of master spec §4.2 and the
separation of product master from listing/offer/price/inventory (REQ-CAT-002,
GAP-0001 closure).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from marketplace.kernel import (
    AuthorizationContext,
    MarketplaceError,
    Stateful,
    generate,
)
from marketplace.kernel.errors import (
    NotFoundError,
    SchemaValidationError,
    ValidationError,
)
from marketplace.kernel.identifiers import IdentifierMappingStore
from marketplace.kernel.state import StateMachine, Transition, register_machine
from marketplace.universes._base import U03

# ---------------------------------------------------------------------------
# Taxonomy & attribute governance (REQ-CAT-003, REQ-CAT-004)
# ---------------------------------------------------------------------------


@dataclass
class TaxonomyNode:
    node_id: str
    code: str
    label: str
    parent_id: Optional[str]
    version: int = 1
    effective_from: str = ""
    effective_to: Optional[str] = None
    locales: dict[str, str] = field(default_factory=dict)


@dataclass
class AttributeDefinition:
    attribute_id: str
    code: str
    label: str
    value_type: str  # text | number | boolean | money | date | reference | enum
    required: bool = False
    unit: Optional[str] = None
    enum_values: list[str] = field(default_factory=list)
    indexed: bool = False
    schema_version: int = 1


@dataclass
class AttributeSchema:
    """A versioned schema binding attributes to a taxonomy node (GOV-12)."""

    schema_id: str
    taxonomy_node_id: str
    version: int
    attributes: list[AttributeDefinition]
    compatibility: str = "backward"
    status: str = "active"


class SchemaRegistry:
    def __init__(self) -> None:
        self._nodes: dict[str, TaxonomyNode] = {}
        self._schemas: dict[tuple[str, int], AttributeSchema] = {}
        self._latest: dict[str, int] = {}
        self._attributes: dict[str, AttributeDefinition] = {}

    def register_taxonomy_node(self, node: TaxonomyNode) -> TaxonomyNode:
        if node.parent_id is not None and node.parent_id not in self._nodes:
            raise ValidationError(f"Unknown parent taxonomy node {node.parent_id}")
        self._nodes[node.node_id] = node
        return node

    def taxonomy(self, node_id: str) -> TaxonomyNode:
        node = self._nodes.get(node_id)
        if node is None:
            raise NotFoundError(f"Taxonomy node {node_id} not found")
        return node

    def ancestors(self, node_id: str) -> list[str]:
        out: list[str] = []
        current: Optional[str] = node_id
        while current is not None and current in self._nodes:
            out.append(current)
            current = self._nodes[current].parent_id
        return out

    def publish_schema(self, schema: AttributeSchema) -> AttributeSchema:
        key = (schema.taxonomy_node_id, schema.version)
        if key in self._schemas:
            raise ValidationError(
                f"Schema version {schema.version} for {schema.taxonomy_node_id} already published"
            )
        if schema.version > 1:
            previous = self._schemas.get((schema.taxonomy_node_id, schema.version - 1))
            if previous is None:
                raise ValidationError(
                    "Schema versions are sequential; cannot publish v"
                    f"{schema.version} without v{schema.version - 1}"
                )
            if schema.compatibility == "backward":
                removed = {a.code for a in previous.attributes} - {a.code for a in schema.attributes}
                if removed:
                    raise SchemaValidationError(
                        f"Backward-incompatible schema: removed attributes {sorted(removed)}; "
                        "declare a breaking change with a migration plan (GOV-12)"
                    )
        self._schemas[key] = schema
        self._latest[schema.taxonomy_node_id] = schema.version
        for attribute in schema.attributes:
            self._attributes[attribute.attribute_id] = attribute
        return schema

    def latest_schema(self, taxonomy_node_id: str) -> AttributeSchema:
        version = self._latest.get(taxonomy_node_id)
        if version is None:
            raise NotFoundError(f"No published schema for {taxonomy_node_id}")
        return self._schemas[(taxonomy_node_id, version)]

    def schema_at(self, taxonomy_node_id: str, version: int) -> AttributeSchema:
        schema = self._schemas.get((taxonomy_node_id, version))
        if schema is None:
            raise NotFoundError(f"No schema {taxonomy_node_id} v{version}")
        return schema

    def attribute(self, attribute_id: str) -> AttributeDefinition:
        attribute = self._attributes.get(attribute_id)
        if attribute is None:
            raise NotFoundError(f"Attribute {attribute_id} not found")
        return attribute

    def validate_attributes(
        self, taxonomy_node_id: str, values: dict[str, Any], *, version: Optional[int] = None
    ) -> dict[str, Any]:
        """REQ-CAT-004: typed extensible attributes with validation."""
        schema = (
            self.schema_at(taxonomy_node_id, version)
            if version is not None
            else self.latest_schema(taxonomy_node_id)
        )
        by_code = {a.code: a for a in schema.attributes}
        validated: dict[str, Any] = {}
        for code, value in values.items():
            definition = by_code.get(code)
            if definition is None:
                raise SchemaValidationError(
                    f"Attribute {code!r} is not defined by schema "
                    f"{taxonomy_node_id} v{schema.version}; unknown attributes are rejected, "
                    "not silently stored"
                )
            validated[code] = self._coerce(definition, value)
        for definition in schema.attributes:
            if definition.required and definition.code not in validated:
                raise SchemaValidationError(
                    f"Required attribute {definition.code!r} is missing"
                )
        return validated

    def _coerce(self, definition: AttributeDefinition, value: Any) -> Any:
        kind = definition.value_type
        if kind == "text":
            if not isinstance(value, str):
                raise SchemaValidationError(f"{definition.code}: expected text")
            return value
        if kind == "number":
            try:
                return float(value)
            except (TypeError, ValueError) as exc:
                raise SchemaValidationError(f"{definition.code}: expected number") from exc
        if kind == "boolean":
            if not isinstance(value, bool):
                raise SchemaValidationError(f"{definition.code}: expected boolean")
            return value
        if kind == "money":
            from marketplace.kernel import Money

            if isinstance(value, Money):
                return value.to_dict()
            raise SchemaValidationError(f"{definition.code}: expected money")
        if kind == "date":
            if not isinstance(value, str):
                raise SchemaValidationError(f"{definition.code}: expected ISO date string")
            return value
        if kind == "enum":
            if value not in definition.enum_values:
                raise SchemaValidationError(
                    f"{definition.code}: {value!r} not in {definition.enum_values}"
                )
            return value
        if kind == "reference":
            if not isinstance(value, str):
                raise SchemaValidationError(f"{definition.code}: expected reference id")
            return value
        raise SchemaValidationError(f"{definition.code}: unknown type {kind!r}")


# ---------------------------------------------------------------------------
# Product master & variants
# ---------------------------------------------------------------------------


@dataclass
class ProductFamily:
    family_id: str
    code: str
    label: str
    version: int = 1


@dataclass
class Brand:
    brand_id: str
    code: str
    label: str
    verified: bool = False


@dataclass
class MediaAsset:
    asset_id: str
    product_id: str
    kind: str  # image | video | document | model3d
    url: str
    rights: str = ""
    provenance: str = ""
    accessibility_alt: str = ""
    moderation_status: str = "pending"


@dataclass
class ProductVersion:
    """An immutable content version of a product (REQ-CAT-009)."""

    version: int
    title: str
    description: str
    attributes: dict[str, Any]
    taxonomy_assignments: list[str]
    brand_id: Optional[str]
    media_ids: list[str]
    created_at: str
    created_by: str
    status: str  # draft | published | archived
    locale: str = "en"


@dataclass
class Product:
    """Canonical sellable concept (master spec §4.1: not a listing or stock record)."""

    product_id: str
    family_id: Optional[str]
    product_type: str  # physical | digital | service | bundle | subscription | rental
    identifier_mappings: dict[str, str] = field(default_factory=dict)
    versions: list[ProductVersion] = field(default_factory=list)
    variants: dict[str, "ProductVariant"] = field(default_factory=dict)
    status: str = "draft"
    version: int = 0

    @property
    def current(self) -> ProductVersion:
        if not self.versions:
            raise MarketplaceError(f"Product {self.product_id} has no content version")
        return self.versions[-1]

    @property
    def published_version(self) -> ProductVersion:
        for version in reversed(self.versions):
            if version.status == "published":
                return version
        raise MarketplaceError(f"Product {self.product_id} has no published content")


@dataclass
class ProductVariant:
    variant_id: str
    product_id: str
    code: str
    attributes: dict[str, Any]
    identifier_mappings: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# State machine: product lifecycle (REQ-CAT-001, GAP closure)
# ---------------------------------------------------------------------------

PRODUCT_MACHINE = register_machine(
    StateMachine(
        name="product.lifecycle",
        concept="product",
        initial_state="draft",
        transitions=(
            Transition("draft", "in_review", frozenset({"catalog.writer", "seller"}), event="product.submitted"),
            Transition("in_review", "published", frozenset({"catalog.approver"}), event="product.published"),
            Transition("in_review", "rejected", frozenset({"catalog.approver"}), event="product.rejected"),
            Transition("rejected", "draft", frozenset({"catalog.writer", "seller"}), event="product.resubmitted"),
            Transition("published", "archived", frozenset({"catalog.admin"}), event="product.archived", terminal=True),
            Transition("published", "blocked", frozenset({"trust.admin"}), event="product.blocked"),
            Transition("blocked", "published", frozenset({"trust.admin"}), event="product.unblocked"),
            Transition("archived", "retired", frozenset({"catalog.admin"}), event="product.retired", terminal=True),
        ),
        terminal_states=frozenset({"retired"}),
    )
)


class ProductEntity(Stateful):
    machine = PRODUCT_MACHINE

    def __init__(self, product_id: str) -> None:
        super().__init__(product_id)
        self.product_id = product_id


# ---------------------------------------------------------------------------
# Catalog service
# ---------------------------------------------------------------------------


class CatalogService:
    """Owns the write path for product master data."""

    def __init__(self, schema_registry: SchemaRegistry, mappings: IdentifierMappingStore) -> None:
        self.schemas = schema_registry
        self.mappings = mappings
        self.families: dict[str, ProductFamily] = {}
        self.brands: dict[str, Brand] = {}
        self.products: dict[str, Product] = {}
        self.media: dict[str, MediaAsset] = {}

    # -- taxonomy & schema ------------------------------------------------

    def define_taxonomy(
        self, authz: AuthorizationContext, code: str, label: str, parent_code: Optional[str] = None, **locales
    ) -> TaxonomyNode:
        authz.requires_role("taxonomy.writer")
        parent_id = None
        if parent_code is not None:
            parent = next(
                (n for n in self.schemas._nodes.values() if n.code == parent_code), None
            )
            if parent is None:
                raise NotFoundError(f"Parent taxonomy {parent_code!r} not found")
            parent_id = parent.node_id
        node = TaxonomyNode(
            node_id=generate("evidence"),
            code=code,
            label=label,
            parent_id=parent_id,
            effective_from=U03.now(),
            locales=locales,
        )
        return self.schemas.register_taxonomy_node(node)

    def publish_attribute_schema(
        self, authz: AuthorizationContext, taxonomy_code: str, attributes: list[AttributeDefinition]
    ) -> AttributeSchema:
        authz.requires_role("schema.publisher")
        node = self._taxonomy_by_code(taxonomy_code)
        version = self.schemas._latest.get(node.node_id, 0) + 1
        return self.schemas.publish_schema(
            AttributeSchema(
                schema_id=generate("evidence"),
                taxonomy_node_id=node.node_id,
                version=version,
                attributes=attributes,
            )
        )

    def _taxonomy_by_code(self, code: str) -> TaxonomyNode:
        node = next((n for n in self.schemas._nodes.values() if n.code == code), None)
        if node is None:
            raise NotFoundError(f"Taxonomy {code!r} not found")
        return node

    # -- product master ---------------------------------------------------

    def create_product(
        self,
        authz: AuthorizationContext,
        product_type: str,
        family_code: Optional[str],
        title: str,
        description: str,
        taxonomy_code: str,
        attributes: dict[str, Any],
        *,
        brand_code: Optional[str] = None,
        schema_version: Optional[int] = None,
        locale: str = "en",
        external_identifiers: Optional[dict[str, str]] = None,
    ) -> Product:
        authz.requires_role("catalog.writer")
        if product_type not in {
            "physical", "digital", "service", "bundle", "subscription", "rental"
        }:
            raise ValidationError(f"Unknown product type {product_type!r}")
        family_id = None
        if family_code is not None:
            family = next((f for f in self.families.values() if f.code == family_code), None)
            if family is None:
                raise NotFoundError(f"Product family {family_code!r} not found")
            family_id = family.family_id
        validated = self.schemas.validate_attributes(
            self._taxonomy_by_code(taxonomy_code).node_id, attributes, version=schema_version
        )
        product = Product(
            product_id=generate("product"),
            family_id=family_id,
            product_type=product_type,
        )
        content = ProductVersion(
            version=1,
            title=title,
            description=description,
            attributes=validated,
            taxonomy_assignments=[self._taxonomy_by_code(taxonomy_code).node_id],
            brand_id=self._brand_id(brand_code),
            media_ids=[],
            created_at=U03.now(),
            created_by=authz.actor_id,
            status="draft",
            locale=locale,
        )
        product.versions.append(content)
        self.products[product.product_id] = product
        # Governed external identifier mapping (external IDs are evidence)
        for system, external_id in (external_identifiers or {}).items():
            self.mappings.map(system, external_id, "product", product.product_id, scope=authz.tenant_id, provenance="catalog.create_product", now=U03.now())
        U03.provenance(
            authz, "create_product", "product", product.product_id,
            reason=f"new {product_type} product in {taxonomy_code}", source="U03.CatalogService",
        )
        return product

    def add_variant(
        self,
        authz: AuthorizationContext,
        product_id: str,
        code: str,
        attributes: dict[str, Any],
        *,
        external_identifiers: Optional[dict[str, str]] = None,
    ) -> ProductVariant:
        authz.requires_role("catalog.writer")
        product = self._require_product(product_id)
        taxonomy_node = product.current.taxonomy_assignments[0]
        validated = self.schemas.validate_attributes(taxonomy_node, attributes)
        variant = ProductVariant(
            variant_id=generate("variant"),
            product_id=product.product_id,
            code=code,
            attributes=validated,
        )
        product.variants[variant.variant_id] = variant
        product.version += 1
        for system, external_id in (external_identifiers or {}).items():
            self.mappings.map(system, external_id, "variant", variant.variant_id, scope=authz.tenant_id, provenance="catalog.add_variant", now=U03.now())
        return variant

    def revise_content(
        self,
        authz: AuthorizationContext,
        product_id: str,
        *,
        title: Optional[str] = None,
        description: Optional[str] = None,
        attributes: Optional[dict[str, Any]] = None,
    ) -> ProductVersion:
        """REQ-CAT-009: content versioning with audit history."""
        authz.requires_role("catalog.writer")
        product = self._require_product(product_id)
        latest = product.current
        taxonomy_node = latest.taxonomy_assignments[0]
        merged_attributes = dict(latest.attributes)
        if attributes:
            merged_attributes.update(self.schemas.validate_attributes(taxonomy_node, attributes))
        version = ProductVersion(
            version=latest.version + 1,
            title=title or latest.title,
            description=description or latest.description,
            attributes=merged_attributes,
            taxonomy_assignments=list(latest.taxonomy_assignments),
            brand_id=latest.brand_id,
            media_ids=list(latest.media_ids),
            created_at=U03.now(),
            created_by=authz.actor_id,
            status="draft",
            locale=latest.locale,
        )
        product.versions.append(version)
        product.version += 1
        return version

    def submit_for_publication(self, authz: AuthorizationContext, product_id: str) -> ProductEntity:
        product = self._require_product(product_id)
        entity = ProductEntity(product_id)
        entity.state = product.status
        entity.transition_to(
            "in_review", actor=authz.actor_id, actor_roles=authz.roles,
            reason="submission for publication", recorded_at=U03.now(),
        )
        product.status = entity.state
        product.current.status = "in_review"
        return entity

    def publish(self, authz: AuthorizationContext, product_id: str) -> Product:
        authz.requires_role("catalog.approver")
        product = self._require_product(product_id)
        entity = ProductEntity(product_id)
        entity.state = product.status
        entity.transition_to(
            "published", actor=authz.actor_id, actor_roles=authz.roles,
            reason="catalog approval", recorded_at=U03.now(),
        )
        product.status = entity.state
        product.current.status = "published"
        U03.provenance(
            authz, "publish_product", "product", product_id,
            reason="publication approved", source="U03.CatalogService",
        )
        return product

    def block(self, authz: AuthorizationContext, product_id: str, reason: str) -> Product:
        authz.requires_role("trust.admin")
        product = self._require_product(product_id)
        entity = ProductEntity(product_id)
        entity.state = product.status
        entity.transition_to(
            "blocked", actor=authz.actor_id, actor_roles=authz.roles,
            reason=reason, recorded_at=U03.now(),
        )
        product.status = entity.state
        return product

    def resolve_by_external(self, system: str, external_id: str, scope: str) -> Optional[Product]:
        canonical_id = self.mappings.resolve(system, external_id, scope)
        if canonical_id is None:
            return None
        return self.products.get(canonical_id)

    def deduplicate(
        self, authz: AuthorizationContext, canonical_id: str, duplicate_id: str, reason: str
    ) -> Product:
        """REQ-CAT-007: identity matching with merge and adjudication."""
        authz.requires_role("catalog.admin")
        canonical = self._require_product(canonical_id)
        duplicate = self._require_product(duplicate_id)
        if canonical.status != duplicate.status:
            raise ValidationError(
                "Merge requires matching lifecycle states; adjudicate the conflict first"
            )
        for system, external_id in {**duplicate.identifier_mappings}.items():
            self.mappings.map(system, external_id, "product", canonical_id, scope=authz.tenant_id, provenance="catalog.deduplicate", now=U03.now())
        for variant_id, variant in duplicate.variants.items():
            canonical.variants.setdefault(variant_id, variant)
        del self.products[duplicate_id]
        canonical.version += 1
        U03.provenance(
            authz, "merge_product", "product", canonical_id,
            reason=reason, source="U03.CatalogService",
            version_before=canonical.version - 1, version_after=canonical.version,
        )
        return canonical

    def _require_product(self, product_id: str) -> Product:
        product = self.products.get(product_id)
        if product is None:
            raise NotFoundError(f"Product {product_id} not found")
        return product

    def _brand_id(self, brand_code: Optional[str]) -> Optional[str]:
        if brand_code is None:
            return None
        brand = next((b for b in self.brands.values() if b.code == brand_code), None)
        if brand is None:
            raise NotFoundError(f"Brand {brand_code!r} not found")
        return brand.brand_id

    def register_brand(self, authz: AuthorizationContext, code: str, label: str) -> Brand:
        authz.requires_role("catalog.writer")
        brand = Brand(brand_id=generate("evidence"), code=code, label=label)
        self.brands[brand.brand_id] = brand
        return brand

    def register_family(self, authz: AuthorizationContext, code: str, label: str) -> ProductFamily:
        authz.requires_role("catalog.writer")
        family = ProductFamily(family_id=generate("evidence"), code=code, label=label)
        self.families[family.family_id] = family
        return family

    def add_media(
        self, authz: AuthorizationContext, product_id: str, kind: str, url: str, *, rights: str = "", alt: str = ""
    ) -> MediaAsset:
        authz.requires_role("catalog.writer")
        product = self._require_product(product_id)
        asset = MediaAsset(
            asset_id=generate("evidence"),
            product_id=product_id,
            kind=kind,
            url=url,
            rights=rights,
            provenance=authz.actor_id,
            accessibility_alt=alt,
        )
        self.media[asset.asset_id] = asset
        product.current.media_ids.append(asset.asset_id)
        return asset


# Concept ownership declarations (GOV-02).
U03.owns("product", "Canonical sellable concept and identifier mappings", "U03.CatalogService.create_product", read_projections=["U04.listing", "U11.search_index"])
U03.owns("product_variant", "Distinguishable product configuration", "U03.CatalogService.add_variant")
U03.owns("product_family", "Grouping of related product definitions", "U03.CatalogService.register_family")
U03.owns("taxonomy", "Versioned classification definitions and validation constraints", "U03.SchemaRegistry.register_taxonomy_node")
U03.owns("attribute_schema", "Versioned typed attribute definitions with compatibility rules", "U03.SchemaRegistry.publish_schema")
U03.owns("brand", "Brand and manufacturer definitions", "U03.CatalogService.register_brand")
U03.owns("media_asset", "Product media with rights, provenance and moderation state", "U03.CatalogService.add_media", read_projections=["U13.moderation"])
