"""Query-protection validation rules for the GraphQL layer.

Standard graphql-core ValidationRules, run during query validation before any
resolver executes — a rejected query never touches MongoDB.

Two rules cover the realistic abuse vectors for this schema:

- depth_limit_rule: caps selection-set nesting. The generated schema is only
  ~4 levels deep, so the default (10) is pure insurance against future nested
  types.
- root_fields_limit_rule: caps root-level selections including aliases. This
  is the vector that matters today — every aliased root field is an
  independent MongoDB query, so `{ a1: get_settlement_fails(...) ... a200:
  get_settlement_fails(...) }` would amplify one request into 200 queries.

Introspection is disabled separately via graphql-core's built-in
NoSchemaIntrospectionCustomRule when GRAPHQL_INTROSPECTION=false (see app.py).
"""
from graphql import GraphQLError
from graphql.language import (
    FieldNode,
    FragmentDefinitionNode,
    FragmentSpreadNode,
    InlineFragmentNode,
    OperationDefinitionNode,
)
from graphql.validation import ValidationRule


def _max_depth(node, fragments: dict, depth: int, visited: frozenset) -> int:
    """Deepest field nesting reachable from node's selection set."""
    selection_set = getattr(node, "selection_set", None)
    if selection_set is None:
        return depth
    deepest = depth
    for sel in selection_set.selections:
        if isinstance(sel, FieldNode):
            deepest = max(deepest, _max_depth(sel, fragments, depth + 1, visited))
        elif isinstance(sel, InlineFragmentNode):
            deepest = max(deepest, _max_depth(sel, fragments, depth, visited))
        elif isinstance(sel, FragmentSpreadNode):
            name = sel.name.value
            if name in visited:  # cyclic spread — NoFragmentCyclesRule reports it
                continue
            frag = fragments.get(name)
            if frag is not None:
                deepest = max(deepest, _max_depth(frag, fragments, depth, visited | {name}))
    return deepest


def _document_fragments(context) -> dict:
    return {
        d.name.value: d
        for d in context.document.definitions
        if isinstance(d, FragmentDefinitionNode)
    }


def depth_limit_rule(max_depth: int) -> type[ValidationRule]:
    class DepthLimitRule(ValidationRule):
        def enter_operation_definition(self, node: OperationDefinitionNode, *_args):
            fragments = _document_fragments(self.context)
            depth = _max_depth(node, fragments, 0, frozenset())
            if depth > max_depth:
                self.report_error(GraphQLError(
                    f"Query depth {depth} exceeds maximum allowed depth {max_depth}.",
                    node,
                ))

    return DepthLimitRule


def root_fields_limit_rule(max_root_fields: int) -> type[ValidationRule]:
    class RootFieldsLimitRule(ValidationRule):
        def enter_operation_definition(self, node: OperationDefinitionNode, *_args):
            count = len(node.selection_set.selections) if node.selection_set else 0
            if count > max_root_fields:
                self.report_error(GraphQLError(
                    f"Query requests {count} root fields (aliases included); "
                    f"maximum allowed is {max_root_fields}.",
                    node,
                ))

    return RootFieldsLimitRule
