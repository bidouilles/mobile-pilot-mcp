"""UI Tree parsing and formatting for iOS accessibility hierarchy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class UIElement:
    """Represents a UI element in the accessibility tree."""

    index: int
    element_type: str
    label: str | None = None
    name: str | None = None
    value: str | None = None
    identifier: str | None = None
    enabled: bool = True
    visible: bool = True
    accessible: bool = True
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    children: list["UIElement"] = field(default_factory=list)

    @property
    def center_x(self) -> int:
        return self.x + self.width // 2

    @property
    def center_y(self) -> int:
        return self.y + self.height // 2

    @property
    def display_text(self) -> str:
        """Get the best text representation for this element."""
        if self.label:
            return self.label
        if self.name:
            return self.name
        if self.value:
            return str(self.value)
        if self.identifier:
            return self.identifier
        return ""

    def to_dict(self, include_children: bool = True) -> dict[str, Any]:
        result = {
            "index": self.index,
            "type": self.element_type,
            "label": self.label,
            "name": self.name,
            "value": self.value,
            "identifier": self.identifier,
            "enabled": self.enabled,
            "visible": self.visible,
            "bounds": {
                "x": self.x,
                "y": self.y,
                "width": self.width,
                "height": self.height,
            },
            "center": {"x": self.center_x, "y": self.center_y},
        }
        if include_children and self.children:
            result["children"] = [c.to_dict(include_children=True) for c in self.children]
        return result


class UITreeParser:
    """Parses WebDriverAgent UI hierarchy into UIElement tree."""

    def __init__(self):
        self._index_counter = 0

    def parse(
        self,
        source: dict[str, Any] | str,
        only_visible: bool = True,
        only_interactable: bool = False,
    ) -> tuple[UIElement | None, list[UIElement]]:
        """Parse WDA source into UIElement tree.

        Args:
            source: WDA source (JSON dict or XML string)
            only_visible: Only include visible elements
            only_interactable: Only include elements that can be interacted with

        Returns:
            Tuple of (root element, flat list of elements with indices)
        """
        self._index_counter = 0

        if isinstance(source, str):
            # XML format - parse it
            return self._parse_xml(source, only_visible, only_interactable)
        else:
            # JSON format from WDA
            return self._parse_json(source, only_visible, only_interactable)

    def _parse_json(
        self,
        data: dict[str, Any],
        only_visible: bool,
        only_interactable: bool,
    ) -> tuple[UIElement | None, list[UIElement]]:
        """Parse JSON hierarchy from WDA."""
        flat_list: list[UIElement] = []

        def parse_element(elem_data: dict[str, Any]) -> UIElement | None:
            # Extract element properties
            elem_type = elem_data.get("type", "Unknown")
            label = elem_data.get("label")
            name = elem_data.get("name")
            value = elem_data.get("value")
            identifier = elem_data.get("identifier")
            enabled = elem_data.get("isEnabled", elem_data.get("enabled", True))
            visible = elem_data.get("isVisible", elem_data.get("visible", True))
            accessible = elem_data.get("isAccessible", elem_data.get("accessible", True))

            # Get bounds
            rect = elem_data.get("rect", {})
            x = int(rect.get("x", 0))
            y = int(rect.get("y", 0))
            width = int(rect.get("width", 0))
            height = int(rect.get("height", 0))

            # Filter based on visibility
            if only_visible and not visible:
                return None

            # Filter based on interactability
            if only_interactable:
                if not enabled or not accessible:
                    return None
                # Skip non-interactive types
                non_interactive = {"Other", "StaticText", "Group", "Cell"}
                if elem_type in non_interactive and not label and not name:
                    return None

            # Create element
            element = UIElement(
                index=self._index_counter,
                element_type=elem_type,
                label=label,
                name=name,
                value=value,
                identifier=identifier,
                enabled=enabled,
                visible=visible,
                accessible=accessible,
                x=x,
                y=y,
                width=width,
                height=height,
            )
            self._index_counter += 1
            flat_list.append(element)

            # Parse children
            children_data = elem_data.get("children", [])
            for child_data in children_data:
                child = parse_element(child_data)
                if child:
                    element.children.append(child)

            return element

        root = parse_element(data)
        return root, flat_list

    def _parse_xml(
        self,
        xml_string: str,
        only_visible: bool,
        only_interactable: bool,
    ) -> tuple[UIElement | None, list[UIElement]]:
        """Parse XML hierarchy from WDA."""
        import xml.etree.ElementTree as ET

        flat_list: list[UIElement] = []

        def parse_element(elem: ET.Element) -> UIElement | None:
            # Extract attributes
            elem_type = elem.get("type", elem.tag)
            label = elem.get("label") or elem.get("accessibilityLabel")
            name = elem.get("name")
            value = elem.get("value")
            identifier = elem.get("accessibilityIdentifier") or elem.get("identifier")
            enabled = elem.get("enabled", "true").lower() == "true"
            visible = elem.get("visible", "true").lower() == "true"
            accessible = elem.get("accessible", "true").lower() == "true"

            # Get bounds
            x = int(float(elem.get("x", 0)))
            y = int(float(elem.get("y", 0)))
            width = int(float(elem.get("width", 0)))
            height = int(float(elem.get("height", 0)))

            # Filter
            if only_visible and not visible:
                return None

            if only_interactable:
                if not enabled or not accessible:
                    return None

            # Create element
            element = UIElement(
                index=self._index_counter,
                element_type=elem_type,
                label=label,
                name=name,
                value=value,
                identifier=identifier,
                enabled=enabled,
                visible=visible,
                accessible=accessible,
                x=x,
                y=y,
                width=width,
                height=height,
            )
            self._index_counter += 1
            flat_list.append(element)

            # Parse children
            for child_elem in elem:
                child = parse_element(child_elem)
                if child:
                    element.children.append(child)

            return element

        try:
            root_elem = ET.fromstring(xml_string)
            root = parse_element(root_elem)
            return root, flat_list
        except ET.ParseError as e:
            raise ValueError(f"Failed to parse XML: {e}") from e

    def format_tree(
        self,
        root: UIElement,
        elements: list[UIElement],
        verbose: bool = False,
    ) -> str:
        """Format the UI tree as a readable string.

        Args:
            root: Root element
            elements: Flat list of elements
            verbose: Include detailed info

        Returns:
            Formatted tree string
        """
        lines: list[str] = []

        def format_element(elem: UIElement, depth: int = 0) -> None:
            indent = "  " * depth
            text = elem.display_text
            text_part = f' "{text}"' if text else ""

            if verbose:
                bounds = f" [{elem.x},{elem.y} {elem.width}x{elem.height}]"
                lines.append(f"{indent}[{elem.index}] {elem.element_type}{text_part}{bounds}")
            else:
                lines.append(f"{indent}[{elem.index}] {elem.element_type}{text_part}")

            for child in elem.children:
                format_element(child, depth + 1)

        format_element(root)
        return "\n".join(lines)

    def format_flat_list(self, elements: list[UIElement], verbose: bool = False) -> str:
        """Format elements as a flat list.

        Args:
            elements: List of elements
            verbose: Include detailed info

        Returns:
            Formatted list string
        """
        lines: list[str] = []

        for elem in elements:
            text = elem.display_text
            text_part = f' "{text}"' if text else ""

            if verbose:
                bounds = f" @ ({elem.center_x},{elem.center_y}) [{elem.width}x{elem.height}]"
                lines.append(f"[{elem.index}] {elem.element_type}{text_part}{bounds}")
            else:
                if text or elem.element_type in (
                    "Button",
                    "TextField",
                    "SecureTextField",
                    "Switch",
                ):
                    lines.append(f"[{elem.index}] {elem.element_type}{text_part}")

        return "\n".join(lines)


def find_element_by_predicate(
    elements: list[UIElement],
    predicate: dict[str, Any],
) -> UIElement | None:
    """Find an element matching the predicate.

    Predicate fields:
        - text: Exact text match (label, name, or value)
        - text_contains: Contains substring (case-insensitive)
        - text_starts_with: Starts with prefix
        - type: Element type (Button, TextField, etc.)
        - label: Accessibility label
        - identifier: Accessibility identifier
        - index: Select Nth match (0-based)
        - bounds_hint: Screen region (top_half, bottom_half, center, etc.)
    """
    text = predicate.get("text")
    text_contains = predicate.get("text_contains")
    text_starts_with = predicate.get("text_starts_with")
    elem_type = predicate.get("type")
    label = predicate.get("label")
    identifier = predicate.get("identifier")
    select_index = predicate.get("index", 0)
    bounds_hint = predicate.get("bounds_hint")

    matches: list[UIElement] = []

    for elem in elements:
        # Type filter
        if elem_type and elem.element_type.lower() != elem_type.lower():
            continue

        # Label filter
        if label and elem.label != label:
            continue

        # Identifier filter
        if identifier and elem.identifier != identifier:
            continue

        # Text matching
        elem_text = elem.display_text.lower()

        if text:
            if elem.display_text != text:
                continue

        if text_contains:
            if text_contains.lower() not in elem_text:
                continue

        if text_starts_with:
            if not elem_text.startswith(text_starts_with.lower()):
                continue

        # Bounds hint filter
        if bounds_hint:
            # Assume screen is roughly 390x844 (iPhone 14 Pro)
            # This is a simplification - real implementation would get screen size
            center_x, center_y = elem.center_x, elem.center_y
            if bounds_hint == "top_half" and center_y > 422:
                continue
            elif bounds_hint == "bottom_half" and center_y <= 422:
                continue
            elif bounds_hint == "left_half" and center_x > 195:
                continue
            elif bounds_hint == "right_half" and center_x <= 195:
                continue
            elif bounds_hint == "center":
                if not (150 < center_x < 240 and 300 < center_y < 544):
                    continue

        matches.append(elem)

    if not matches:
        return None

    if select_index >= len(matches):
        return matches[-1]

    return matches[select_index]
