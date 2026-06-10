from __future__ import annotations

from pathlib import Path
from typing import Any

import plotly.graph_objects as go
import plotly.io as pio


COLORS = [
    "#ef4444", "#f59e0b", "#84cc16", "#22c55e", "#14b8a6",
    "#06b6d4", "#3b82f6", "#6366f1", "#a855f7", "#ec4899",
    "#f97316", "#10b981", "#0ea5e9", "#8b5cf6", "#d946ef",
]


def box_mesh(
    x: float,
    y: float,
    z: float,
    length: float,
    width: float,
    height: float,
    color: str,
    name: str,
    hover_text: str,
    opacity: float = 1.0,
) -> go.Mesh3d:
    """
    Creates a rectangular prism for Plotly.
    """

    x0, x1 = x, x + length
    y0, y1 = y, y + width
    z0, z1 = z, z + height

    vertices_x = [x0, x1, x1, x0, x0, x1, x1, x0]
    vertices_y = [y0, y0, y1, y1, y0, y0, y1, y1]
    vertices_z = [z0, z0, z0, z0, z1, z1, z1, z1]

    # 12 triangles, 2 for each face.
    i = [0, 0, 0, 4, 4, 4, 0, 1, 2, 3, 0, 1]
    j = [1, 2, 4, 5, 6, 0, 3, 2, 3, 0, 5, 6]
    k = [2, 3, 5, 6, 7, 7, 7, 6, 7, 4, 6, 2]

    return go.Mesh3d(
        x=vertices_x,
        y=vertices_y,
        z=vertices_z,
        i=i,
        j=j,
        k=k,
        color=color,
        opacity=opacity,
        name=name,
        hovertext=hover_text,
        hoverinfo="text",
        flatshading=True,
        showscale=False,
    )


def make_plan_figure(plan: Any, title: str) -> go.Figure:
    """
    Builds a 3D Plotly figure for one skid plan.
    """

    fig = go.Figure()

    # Thin skid base.
    fig.add_trace(
        box_mesh(
            x=0,
            y=0,
            z=-0.35,
            length=plan.skid_length,
            width=plan.skid_width,
            height=0.35,
            color="#d6c2a1",
            name="Skid base",
            hover_text=(
                f"Skid base<br>"
                f"Length: {round(plan.skid_length, 2)}<br>"
                f"Width: {round(plan.skid_width, 2)}<br>"
                f"Height: {round(plan.skid_height, 2)}"
            ),
            opacity=0.45,
        )
    )

    color_index = 0

    for layer in plan.layers:
        for placement in layer.placements:
            color = COLORS[color_index % len(COLORS)]
            color_index += 1

            hover_text = (
                f"CSV/Excel row: {placement.csv_row_number}<br>"
                f"Copy: {placement.copy_number}<br>"
                f"Layer: {placement.layer_number}<br>"
                f"Position: x={round(placement.x, 2)}, "
                f"y={round(placement.y, 2)}, z={round(placement.z, 2)}<br>"
                f"Size: {round(placement.length, 2)} x "
                f"{round(placement.width, 2)} x "
                f"{round(placement.height, 2)}<br>"
                f"{placement.orientation}"
            )

            fig.add_trace(
                box_mesh(
                    x=placement.x,
                    y=placement.y,
                    z=placement.z,
                    length=placement.length,
                    width=placement.width,
                    height=placement.height,
                    color=color,
                    name=f"Row {placement.csv_row_number}",
                    hover_text=hover_text,
                    opacity=1.0,
                )
            )

    fig.update_layout(
        title=title,
        scene=dict(
            xaxis_title="Skid Length / X",
            yaxis_title="Skid Width / Y",
            zaxis_title="Skid Height / Z",
            xaxis=dict(range=[0, max(plan.skid_length, 1)]),
            yaxis=dict(range=[0, max(plan.skid_width, 1)]),
            zaxis=dict(range=[0, max(plan.skid_height, 1)]),
            aspectmode="data",
            camera=dict(
                eye=dict(x=1.6, y=1.6, z=0.9)
            ),
        ),
        margin=dict(l=0, r=0, t=50, b=0),
        height=700,
        showlegend=False,
    )

    return fig


def export_all_skids_to_plotly_html(
    group_plans: dict[str, Any],
    group_results: dict[str, dict[str, Any]],
    output_html_path: str,
) -> None:
    """
    Writes one HTML file containing every group's skid visualization.

    Each group gets its own Plotly 3D figure stacked vertically in the page.
    """

    output_path = Path(output_html_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    html_parts = [
        "<!DOCTYPE html>",
        "<html>",
        "<head>",
        "<meta charset='utf-8'>",
        "<title>All Skid Plans</title>",
        "<style>",
        "body { font-family: Arial, sans-serif; margin: 24px; background: #f8fafc; color: #0f172a; }",
        ".summary { padding: 12px 16px; background: white; border: 1px solid #e2e8f0; border-radius: 10px; margin-bottom: 18px; }",
        ".skid-card { padding: 16px; background: white; border: 1px solid #e2e8f0; border-radius: 10px; margin: 24px 0; }",
        ".meta { color: #475569; margin-bottom: 10px; }",
        "</style>",
        "</head>",
        "<body>",
        "<h1>All Skid Plans</h1>",
        f"<div class='summary'>Total visualized skid group(s): {len(group_plans)}</div>",
    ]

    first_plot = True

    for group_key, plan in group_plans.items():
        result = group_results.get(group_key, {})

        title = (
            f"Group {group_key} | "
            f"{result.get('cartons_in_group', '?')} carton(s) | "
            f"Skid {round(plan.skid_length, 2)} x "
            f"{round(plan.skid_width, 2)} x "
            f"{round(plan.skid_height, 2)}"
        )

        fig = make_plan_figure(plan, title=title)

        include_plotlyjs = "cdn" if first_plot else False
        first_plot = False

        html_parts.append("<div class='skid-card'>")
        html_parts.append(f"<h2>Group {group_key}</h2>")
        html_parts.append(
            "<div class='meta'>"
            f"Group value: {result.get('group_value', '')} | "
            f"Cartons: {result.get('cartons_in_group', '')} | "
            f"Skid: {round(plan.skid_length, 2)} x "
            f"{round(plan.skid_width, 2)} x "
            f"{round(plan.skid_height, 2)}"
            "</div>"
        )
        html_parts.append(
            pio.to_html(
                fig,
                full_html=False,
                include_plotlyjs=include_plotlyjs,
            )
        )
        html_parts.append("</div>")

    html_parts.extend(["</body>", "</html>"])

    output_path.write_text("\n".join(html_parts), encoding="utf-8")

    print(f"All skid visualizations written to: {output_path}")
