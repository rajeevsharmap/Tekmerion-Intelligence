"""
visualize_network.py
====================

Case-scoped financial crime visualizer.

Visualization rules
-------------------

smurfing
    -> directed NetworkX fraud network

reverse_smurfing
    -> directed NetworkX fraud network

money_mule
    -> transaction amount vs time

account_swap
    -> behavioral transaction amount vs time

Important
---------

This script ONLY visualizes already-generated evidence.

It does NOT:
    - run detection
    - regenerate evidence
    - traverse the transaction database
    - create PNG files
    - modify cases

Evidence source:

    pipeline_output/evidence/<CASE_ID>.json
"""

import json
import os
from datetime import datetime

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import networkx as nx


# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------

EVIDENCE_DIR = os.path.join(
    "pipeline_output",
    "evidence",
)

GRAPH_TYPOLOGIES = {
    "smurfing",
    "reverse_smurfing",
}

TIMELINE_TYPOLOGIES = {
    "money_mule",
    "account_swap",
}

ROLE_COLORS = {
    "root": "#d62728",
    "source": "#1f77b4",
    "downstream": "#2ca02c",
    "distribution_target": "#2ca02c",
    "related": "#7f7f7f",
}


# ----------------------------------------------------------------------
# Generic helpers
# ----------------------------------------------------------------------

def _load_case_evidence(case_id):
    """Load persisted case evidence."""

    path = os.path.join(
        EVIDENCE_DIR,
        f"{case_id}.json",
    )

    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"No evidence file found for case {case_id}.\n"
            f"Expected:\n{path}"
        )

    with open(
        path,
        "r",
        encoding="utf-8",
    ) as f:
        evidence = json.load(f)

    if not isinstance(evidence, dict):
        raise ValueError(
            "Evidence file does not contain a JSON object."
        )

    return evidence, path


def _parse_time(value):
    """Parse ISO timestamp."""

    if not value:
        return None

    if isinstance(value, datetime):
        return value

    return datetime.fromisoformat(value)


def _fmt_amount(amount):
    """Format INR transaction amounts."""

    if amount is None:
        return ""

    amount = float(amount)

    if amount >= 100000:
        return f"₹{amount / 100000:.1f}L"

    if amount >= 1000:
        return f"₹{amount / 1000:.0f}k"

    return f"₹{amount:.0f}"


def _extract_pattern_text(pattern):
    """
    Convert structured pattern dictionaries into readable text.

    Handles:

        {"type": "many_to_one", ...}

    as well as:

        "many_to_one"
    """

    if isinstance(pattern, str):
        return pattern

    if not isinstance(pattern, dict):
        return str(pattern)

    pattern_type = pattern.get(
        "type",
        "unknown",
    )

    details = []

    for key, value in pattern.items():

        if key == "type":
            continue

        if isinstance(value, list):

            if all(
                isinstance(item, str)
                for item in value
            ):
                value_text = ", ".join(value)

            else:
                value_text = str(value)

        else:
            value_text = str(value)

        details.append(
            f"{key}={value_text}"
        )

    if details:
        return (
            f"{pattern_type} "
            f"({'; '.join(details)})"
        )

    return pattern_type


def _pattern_summary(patterns):
    """Create readable pattern summary."""

    if not patterns:
        return "No named patterns detected"

    return "\n".join(
        f"• {_extract_pattern_text(pattern)}"
        for pattern in patterns
    )


# ----------------------------------------------------------------------
# Network evidence
# ----------------------------------------------------------------------

def _build_graph(data):
    """
    Build NetworkX DiGraph directly from persisted evidence.
    """

    graph = nx.DiGraph()

    # --------------------------------------------------------------
    # Nodes
    # --------------------------------------------------------------

    for node in data.get("nodes", []):

        node_data = node.get(
            "data",
            {},
        )

        node_id = node_data.get("id")

        if not node_id:
            continue

        graph.add_node(
            node_id,
            label=node_data.get(
                "label",
                node_id,
            ),
            role=node_data.get(
                "role",
                "related",
            ),
            risk=node_data.get(
                "risk",
                "unknown",
            ),
        )

    # --------------------------------------------------------------
    # Edges
    # --------------------------------------------------------------

    for edge in data.get("edges", []):

        edge_data = edge.get(
            "data",
            {},
        )

        source = edge_data.get(
            "source"
        )

        target = edge_data.get(
            "target"
        )

        if not source or not target:
            continue

        graph.add_edge(
            source,
            target,
            transaction_id=edge_data.get("id"),
            amount=edge_data.get("amount"),
            currency=edge_data.get(
                "currency",
                "INR",
            ),
            timestamp=edge_data.get(
                "timestamp"
            ),
            depth=edge_data.get(
                "depth"
            ),
        )

    return graph


def _layered_positions(graph, root):
    """
    Arrange graph around root.

    Negative X:
        upstream

    Zero:
        root

    Positive X:
        downstream
    """

    depth = {
        root: 0
    }

    # --------------------------------------------------------------
    # Upstream
    # --------------------------------------------------------------

    frontier = [root]
    visited = {root}
    current_depth = 0

    while frontier:

        current_depth -= 1
        next_frontier = []

        for node in frontier:

            for predecessor in graph.predecessors(
                node
            ):

                if predecessor not in visited:

                    visited.add(predecessor)

                    depth[
                        predecessor
                    ] = current_depth

                    next_frontier.append(
                        predecessor
                    )

        frontier = next_frontier

    # --------------------------------------------------------------
    # Downstream
    # --------------------------------------------------------------

    frontier = [root]
    visited = {root}
    current_depth = 0

    while frontier:

        current_depth += 1
        next_frontier = []

        for node in frontier:

            for successor in graph.successors(
                node
            ):

                if successor not in visited:

                    visited.add(successor)

                    depth[
                        successor
                    ] = current_depth

                    next_frontier.append(
                        successor
                    )

        frontier = next_frontier

    # --------------------------------------------------------------
    # Disconnected nodes
    # --------------------------------------------------------------

    for node in graph.nodes():

        depth.setdefault(
            node,
            0,
        )

    # --------------------------------------------------------------
    # Position nodes
    # --------------------------------------------------------------

    levels = {}

    for node, level in depth.items():

        levels.setdefault(
            level,
            [],
        ).append(node)

    positions = {}

    for level, nodes in levels.items():

        nodes = sorted(nodes)

        count = len(nodes)

        for index, node in enumerate(nodes):

            positions[node] = (
                level,
                index - (count - 1) / 2,
            )

    return positions


def visualize_network(
    case_id,
    evidence,
):
    """Render smurfing/reverse-smurfing network."""

    data = evidence.get(
        "data",
        {},
    )

    root = data.get(
        "root_account"
    )

    if not root:
        raise ValueError(
            f"Network evidence for {case_id} "
            "does not contain root_account."
        )

    graph = _build_graph(
        data
    )

    if not graph.nodes:
        raise ValueError(
            "Network evidence contains no nodes."
        )

    if not graph.edges:
        raise ValueError(
            "Network evidence contains no edges."
        )

    if root not in graph:
        raise ValueError(
            f"Root account {root} "
            "is not present in graph."
        )

    typology = evidence.get(
        "typology",
        "unknown",
    )

    # --------------------------------------------------------------
    # Layout
    # --------------------------------------------------------------

    pos = _layered_positions(
        graph,
        root,
    )

    # --------------------------------------------------------------
    # Node styling
    # --------------------------------------------------------------

    node_colors = []

    node_sizes = []

    for node in graph.nodes():

        role = graph.nodes[node].get(
            "role",
            "related",
        )

        node_colors.append(
            ROLE_COLORS.get(
                role,
                ROLE_COLORS["related"],
            )
        )

        node_sizes.append(
            1800
            if node == root
            else 1000
        )

    # --------------------------------------------------------------
    # Edge widths
    # --------------------------------------------------------------

    amounts = [
        float(
            data.get("amount") or 0
        )
        for _, _, data
        in graph.edges(
            data=True
        )
    ]

    minimum = (
        min(amounts)
        if amounts
        else 0
    )

    maximum = (
        max(amounts)
        if amounts
        else 0
    )

    edge_widths = []

    for _, _, edge_data in graph.edges(
        data=True
    ):

        amount = float(
            edge_data.get("amount") or 0
        )

        if maximum == minimum:

            width = 2.5

        else:

            width = (
                1.2
                + 4.5
                * (
                    amount - minimum
                )
                / (
                    maximum - minimum
                )
            )

        edge_widths.append(
            width
        )

    # --------------------------------------------------------------
    # Figure
    # --------------------------------------------------------------

    fig, ax = plt.subplots(
        figsize=(15, 9)
    )

    # Nodes

    nx.draw_networkx_nodes(
        graph,
        pos,
        node_color=node_colors,
        node_size=node_sizes,
        edgecolors="black",
        linewidths=1.2,
        ax=ax,
    )

    # Labels

    labels = {
        node: graph.nodes[node].get(
            "label",
            node,
        )
        for node in graph.nodes()
    }

    nx.draw_networkx_labels(
        graph,
        pos,
        labels=labels,
        font_size=8,
        font_weight="bold",
        ax=ax,
    )

    # Directed edges

    nx.draw_networkx_edges(
        graph,
        pos,
        width=edge_widths,
        edge_color="#555555",
        arrows=True,
        arrowsize=18,
        arrowstyle="-|>",
        connectionstyle="arc3,rad=0.08",
        node_size=node_sizes,
        ax=ax,
    )

    # --------------------------------------------------------------
    # Amount labels
    # --------------------------------------------------------------

    if graph.number_of_edges() <= 30:

        edge_labels = {}

        for source, target, edge_data in graph.edges(
            data=True
        ):

            edge_labels[
                (source, target)
            ] = _fmt_amount(
                edge_data.get(
                    "amount"
                )
            )

        nx.draw_networkx_edge_labels(
            graph,
            pos,
            edge_labels=edge_labels,
            font_size=7,
            label_pos=0.5,
            ax=ax,
        )

    # --------------------------------------------------------------
    # Legend
    # --------------------------------------------------------------

    legend_definitions = [
        ("Root account", "root"),
        ("Source account", "source"),
        (
            "Downstream / target",
            "downstream",
        ),
        ("Related account", "related"),
    ]

    handles = [
        mpatches.Patch(
            color=ROLE_COLORS[role],
            label=label,
        )
        for label, role
        in legend_definitions
    ]

    ax.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.03),
        ncol=4,
        frameon=False,
    )

    # --------------------------------------------------------------
    # Patterns
    # --------------------------------------------------------------

    pattern_text = _pattern_summary(
        data.get(
            "patterns",
            [],
        )
    )

    # --------------------------------------------------------------
    # Title
    # --------------------------------------------------------------

    ax.set_title(
        f"{typology.replace('_', ' ').title()} Network\n"
        f"Case: {case_id} | Root: {root}\n"
        f"Nodes: {graph.number_of_nodes()} | "
        f"Transactions: {graph.number_of_edges()}",
        fontsize=13,
        fontweight="bold",
        pad=20,
    )

    # --------------------------------------------------------------
    # Pattern panel
    # --------------------------------------------------------------

    ax.text(
        1.02,
        0.5,
        "Detected Patterns\n\n"
        + pattern_text,
        transform=ax.transAxes,
        fontsize=8,
        verticalalignment="center",
        bbox=dict(
            boxstyle="round,pad=0.5",
            facecolor="white",
            edgecolor="gray",
            alpha=0.9,
        ),
    )

    ax.axis("off")

    plt.tight_layout()

    # No PNG.
    plt.show()

    return {
        "case_id": case_id,
        "account_id": root,
        "typology": typology,
        "num_nodes": graph.number_of_nodes(),
        "num_edges": graph.number_of_edges(),
        "patterns": data.get(
            "patterns",
            [],
        ),
        "graph": graph,
    }


# ----------------------------------------------------------------------
# Money mule
# ----------------------------------------------------------------------

def visualize_money_mule(
    case_id,
    evidence,
):
    """
    Visualize money-mule evidence as transaction amount vs time.

    Expected evidence structure:

        data:
            visualization_type:
                transaction_timeline

            transactions:
                [...]
    """

    data = evidence.get(
        "data",
        {},
    )

    transactions = data.get(
        "transactions",
        [],
    )

    if not transactions:
        raise ValueError(
            f"No transactions found in money-mule "
            f"evidence for {case_id}."
        )

    inbound_x = []
    inbound_y = []

    outbound_x = []
    outbound_y = []

    for transaction in transactions:

        timestamp = _parse_time(
            transaction.get(
                "timestamp"
            )
        )

        amount = transaction.get(
            "amount"
        )

        if timestamp is None or amount is None:
            continue

        direction = transaction.get(
            "direction"
        )

        if direction == "in":

            inbound_x.append(
                timestamp
            )

            inbound_y.append(
                float(amount)
            )

        elif direction == "out":

            outbound_x.append(
                timestamp
            )

            outbound_y.append(
                float(amount)
            )

    fig, ax = plt.subplots(
        figsize=(14, 6)
    )

    ax.scatter(
        inbound_x,
        inbound_y,
        s=70,
        label="Inbound",
    )

    ax.scatter(
        outbound_x,
        outbound_y,
        s=70,
        label="Outbound",
    )

    ax.set_xlabel(
        "Time"
    )

    ax.set_ylabel(
        "Transaction Amount (INR)"
    )

    ax.set_title(
        f"Money Mule — Transaction Timeline\n"
        f"Case: {case_id}"
    )

    ax.legend()

    fig.autofmt_xdate()

    plt.tight_layout()

    plt.show()

    return {
        "case_id": case_id,
        "account_id": data.get(
            "account_id"
        ),
        "typology": "money_mule",
        "patterns": data.get(
            "patterns",
            [],
        ),
    }


# ----------------------------------------------------------------------
# Account swap
# ----------------------------------------------------------------------

def visualize_account_swap(
    case_id,
    evidence,
):
    """
    Visualize account-swap behavioral evidence.

    Transactions:
        scatter points

    Device / SIM / GEO:
        vertical event markers

    Baseline:
        horizontal behavioral baseline
    """

    data = evidence.get(
        "data",
        {},
    )

    events = data.get(
        "events",
        [],
    )

    behavioral = data.get(
        "behavioral_summary",
        {},
    )

    if not events:
        raise ValueError(
            f"No events found in account-swap "
            f"evidence for {case_id}."
        )

    # --------------------------------------------------------------
    # Separate events
    # --------------------------------------------------------------

    transaction_times = []
    transaction_amounts = []

    event_times = []
    event_labels = []

    for event in events:

        timestamp = _parse_time(
            event.get(
                "timestamp"
            )
        )

        if timestamp is None:
            continue

        event_type = event.get(
            "event_type",
            "event",
        )

        if event_type == "transaction":

            amount = event.get(
                "amount"
            )

            if amount is not None:

                transaction_times.append(
                    timestamp
                )

                transaction_amounts.append(
                    float(amount)
                )

        else:

            event_times.append(
                timestamp
            )

            event_labels.append(
                event_type.replace(
                    "_",
                    " ",
                )
            )

    # --------------------------------------------------------------
    # Figure
    # --------------------------------------------------------------

    fig, ax = plt.subplots(
        figsize=(15, 7)
    )

    # Transactions

    ax.scatter(
        transaction_times,
        transaction_amounts,
        s=90,
        label="Transactions",
        zorder=4,
    )

    # Transaction labels

    for timestamp, amount in zip(
        transaction_times,
        transaction_amounts,
    ):

        ax.annotate(
            _fmt_amount(amount),
            (
                timestamp,
                amount,
            ),
            xytext=(5, 7),
            textcoords="offset points",
            fontsize=8,
        )

    # Behavioral events

    event_y = 0

    for timestamp, label in zip(
        event_times,
        event_labels,
    ):

        ax.axvline(
            timestamp,
            linestyle="--",
            alpha=0.45,
        )

        ax.annotate(
            label,
            xy=(
                timestamp,
                event_y,
            ),
            xytext=(
                0,
                15,
            ),
            textcoords="offset points",
            rotation=90,
            va="bottom",
            ha="center",
            fontsize=8,
        )

    # --------------------------------------------------------------
    # Baseline
    # --------------------------------------------------------------

    baseline_amount = behavioral.get(
        "baseline_avg_amount"
    )

    if baseline_amount is not None:

        ax.axhline(
            float(baseline_amount),
            linestyle=":",
            linewidth=1.8,
            label=(
                "Baseline avg amount "
                f"₹{float(baseline_amount):,.2f}"
            ),
        )

    # --------------------------------------------------------------
    # Title
    # --------------------------------------------------------------

    amount_ratio = behavioral.get(
        "amount_deviation_ratio"
    )

    title = (
        "Account Swap — Behavioral "
        "Transaction Timeline\n"
        f"Case: {case_id}"
    )

    if amount_ratio is not None:

        title += (
            f"\nAmount deviation: "
            f"{float(amount_ratio):.2f}× baseline"
        )

    ax.set_title(
        title,
        fontsize=12,
    )

    ax.set_xlabel(
        "Time"
    )

    ax.set_ylabel(
        "Transaction Amount (INR)"
    )

    ax.legend(
        loc="upper left"
    )

    fig.autofmt_xdate()

    plt.tight_layout()

    plt.show()

    return {
        "case_id": case_id,
        "account_id": data.get(
            "account_id"
        ),
        "typology": "account_swap",
        "patterns": data.get(
            "patterns",
            [],
        ),
        "behavioral_summary": behavioral,
    }


# ----------------------------------------------------------------------
# Unified dispatcher
# ----------------------------------------------------------------------

def visualize_case(case_id):

    evidence, evidence_path = (
        _load_case_evidence(
            case_id
        )
    )

    typology = evidence.get(
        "typology"
    )

    if not typology:
        raise ValueError(
            f"Evidence for {case_id} "
            "does not contain typology."
        )

    data = evidence.get(
        "data",
        {},
    )

    # --------------------------------------------------------------
    # Account resolution
    # --------------------------------------------------------------

    account_id = (
        data.get("root_account")
        or data.get("account_id")
    )

    print()
    print("=" * 70)
    print("CASE VISUALIZATION")
    print("=" * 70)

    print(
        f"Case ID       : {case_id}"
    )

    print(
        f"Account ID    : {account_id}"
    )

    print(
        f"Typology      : {typology}"
    )

    print(
        f"Evidence file : {evidence_path}"
    )

    print()

    # --------------------------------------------------------------
    # Dispatch
    # --------------------------------------------------------------

    if typology in GRAPH_TYPOLOGIES:

        return visualize_network(
            case_id,
            evidence,
        )

    if typology == "money_mule":

        return visualize_money_mule(
            case_id,
            evidence,
        )

    if typology == "account_swap":

        return visualize_account_swap(
            case_id,
            evidence,
        )

    raise ValueError(
        f"Unsupported typology: {typology!r}"
    )


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def main():

    print()
    print("=" * 70)
    print("FINANCIAL CRIME CASE VISUALIZER")
    print("=" * 70)
    print()

    if not os.path.isdir(
        EVIDENCE_DIR
    ):

        print(
            "Evidence directory not found:"
        )

        print(
            EVIDENCE_DIR
        )

        return

    evidence_files = [
        filename
        for filename in os.listdir(
            EVIDENCE_DIR
        )
        if filename.endswith(".json")
    ]

    print(
        f"Available case-scoped evidence: "
        f"{len(evidence_files)} cases"
    )

    print()
    print(
        "Enter a case ID manually."
    )

    print(
        "Example: CASE-597BBE77"
    )

    print()

    case_id = input(
        "Case ID: "
    ).strip()

    if not case_id:

        print(
            "\nNo case ID entered."
        )

        return

    try:

        result = visualize_case(
            case_id
        )

        print()
        print("=" * 70)
        print("VISUALIZATION COMPLETE")
        print("=" * 70)

        print(
            f"Case ID    : "
            f"{result['case_id']}"
        )

        print(
            f"Account ID : "
            f"{result.get('account_id')}"
        )

        print(
            f"Typology   : "
            f"{result['typology']}"
        )

        if "num_nodes" in result:

            print(
                f"Nodes      : "
                f"{result['num_nodes']}"
            )

            print(
                f"Edges      : "
                f"{result['num_edges']}"
            )

        print()

    except Exception as exc:

        print()
        print("=" * 70)
        print("VISUALIZATION FAILED")
        print("=" * 70)

        print(
            str(exc)
        )

        print()


if __name__ == "__main__":
    main()