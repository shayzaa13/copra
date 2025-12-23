from __future__ import annotations

from pathlib import Path
from textwrap import indent
from typing import Dict, List, Set, Any, Tuple, Callable

from .discovery import HierarchyDict
from .config import get_config
from .introspection import sanitize_name

class StubGenerator:
    """Configurable stub file generator."""
    
    def __init__(self):
        self.config = get_config()
    
    def generate_stub(self, hierarchy: HierarchyDict, out_dir: Path) -> Path:
        """Generate comprehensive stub file from HierarchyDict with proper cocotb types."""
        lines: list[str] = []
        
        # Imports
        lines.extend(self.config.types.import_statements)
        lines.append("from typing import NewType")
        lines.append("")
        
        # We scan the flat list of nodes to find every unique LogicArray width used in the design
        found_widths = set()
        for node in hierarchy.get_nodes():
            if node.width is not None and "LogicArrayObject" in getattr(node, "py_type", ""):
                found_widths.add(node.width)

        # We define unique types for each width (e.g., LogicArray8) to enable stricter type checking
        for w in sorted(found_widths):
            lines.append(f'LogicArray{w} = NewType("LogicArray{w}", cocotb.handle.LogicArrayObject)')
        
        lines.append("")
        
        for header_line in self.config.output.header_lines:
            lines.append(f"# {header_line}")
        lines.append("")
        
        tree = hierarchy.get_tree()
        if not tree:
            lines.extend([
                f"class {self.config.output.root_class_name}({self.config.types.base_classes['hierarchy']}):",
                "    pass",
                "",
            ])
        else:
            top_key = list(tree.keys())[0]
            top_tree = tree[top_key]
            self._generate_classes(tree, lines, top_key, top_tree)
        
        out_dir.mkdir(parents=True, exist_ok=True)
        content = "\n".join(lines)
        
        stub_path = out_dir / self.config.output.stub_filename
        stub_path.write_text(content, encoding="utf-8")
        return stub_path

    def _generate_classes(self, tree, lines, top_key, top_tree):
        top_node = top_tree.get("_node")
        top_class_name = sanitize_name(top_key)
        
        base_class_key = 'hierarchy'
        if top_node and self.config.types.base_classes['hierarchy_array'].split('.')[-1] in top_node.py_type:
            base_class_key = 'hierarchy_array'
        
        base_class = self.config.types.base_classes[base_class_key]
        lines.append(f"class {top_class_name}({base_class}):")
        
        children = top_tree.get("_children", {})
        if not children:
            lines.append("    pass")
        else:
            self._generate_class_attributes(lines, children, "    ")
            self._generate_getitem_overloads(lines, children, "    ")
        lines.append("")
        
        generated_classes: Set[str] = set()
        self._generate_meaningful_classes(tree, lines, generated_classes, top_class_name)

    def _generate_class_attributes(self, lines: List[str], children: Dict[str, Any], indent_str: str, filter_deep_signals: bool = False) -> None:
        for child_name, child_tree in sorted(children.items()):
            if '[' in child_name and child_name.endswith(']'): continue
            child_node = child_tree.get("_node")
            if child_node:
                if filter_deep_signals and not child_node.is_scope and child_node.path.count('.') > 1: continue
                
                can_be_attr = child_name.isidentifier() and (child_node.is_scope or not child_name.startswith('_'))
                if can_be_attr:
                    type_ann = child_node.py_type
                    if child_node.is_scope:
                        cls_name = sanitize_name(child_name)
                        if self.config.types.base_classes['hierarchy_array'].split('.')[-1] in child_node.py_type:
                            type_ann = child_node.py_type if "[" in child_node.py_type else f"{self.config.types.base_classes['hierarchy_array']}[{cls_name}]"
                        else:
                            type_ann = cls_name
                    elif "LogicArrayObject" in type_ann and child_node.width is not None:
                        type_ann = f"LogicArray{child_node.width}"
                    
                    lines.append(f"{indent_str}{child_name}: {type_ann}")

    def _generate_getitem_overloads(self, lines: List[str], children: Dict[str, Any], indent_str: str, filter_deep_signals: bool = False) -> None:
        overloads = []
        for name, tree in children.items():
            if '[' in name and name.endswith(']'): continue
            node = tree.get("_node")
            if node:
                if filter_deep_signals and not node.is_scope and node.path.count('.') > 1: continue
                
                type_ann = node.py_type
                if node.is_scope:
                    cls = sanitize_name(name)
                    base = self.config.types.base_classes['hierarchy_array']
                    if base.split('.')[-1] in node.py_type:
                        type_ann = node.py_type if "[" in node.py_type else f"{base}[{cls}]"
                    else:
                        type_ann = cls
                # === FIX START: Update LogicArrayObject to use the specific LogicArrayX type ===
                elif "LogicArrayObject" in type_ann and node.width is not None:
                    type_ann = f"LogicArray{node.width}"
                # === FIX END ===
                
                overloads.append((name, type_ann))
        
        if overloads:
            lines.append("")
            for n, t in overloads:
                lines.append(f"{indent_str}@overload")
                lines.append(f"{indent_str}def __getitem__(self, name: Literal[{repr(n)}]) -> {t}: ...")
                lines.append("")
            lines.append(f"{indent_str}@overload")
            lines.append(f"{indent_str}def __getitem__(self, name: str) -> cocotb.handle.SimHandleBase: ...")
            lines.append("")

    def _generate_meaningful_classes(self, tree: Dict[str, Any], lines: List[str], generated_classes: Set[str], top_class_name: str) -> None:
        for name, subtree in sorted(tree.items()):
            node = subtree.get("_node")
            children = subtree.get("_children", {})
            if node and node.is_scope and children and name != list(tree.keys())[0]:
                cls_name = sanitize_name(name)
                if cls_name not in generated_classes and cls_name != top_class_name:
                    generated_classes.add(cls_name)
                    base = self.config.types.base_classes['hierarchy_array' if self.config.types.base_classes['hierarchy_array'].split('.')[-1] in node.py_type else 'hierarchy']
                    lines.append(f"class {cls_name}({base}):")
                    
                    has_children = False
                    for cname, ctree in sorted(children.items()):
                        if '[' in cname and cname.endswith(']'): continue
                        cnode = ctree.get("_node")
                        if cnode:
                            has_children = True
                            ctann = cnode.py_type
                            if cnode.is_scope:
                                ccls = sanitize_name(cname)
                                if self.config.types.base_classes['hierarchy_array'].split('.')[-1] in cnode.py_type:
                                    ctann = cnode.py_type if "[" in cnode.py_type else f"{self.config.types.base_classes['hierarchy_array']}[{ccls}]"
                                else:
                                    ctann = ccls
                            # === FIX START: Also update nested attributes ===
                            elif "LogicArrayObject" in ctann and cnode.width is not None:
                                ctann = f"LogicArray{cnode.width}"
                            # === FIX END ===
                                
                            lines.append(indent(f"{cname}: {ctann}", "    "))
                    
                    if not has_children: lines.append("    pass")
                    else: self._generate_getitem_overloads(lines, children, "    ")
                    lines.append("")
            self._generate_meaningful_classes(children, lines, generated_classes, top_class_name)

def generate_stub(hierarchy: HierarchyDict, out_dir: Path) -> Path:
    return StubGenerator().generate_stub(hierarchy, out_dir)