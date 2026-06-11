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


def darker_hex(hex_color: str, factor: float = 0.55) -> str:
    """
    Creates a darker version of a color for carton edge outlines.
    """

    hex_color = hex_color.lstrip("#")

    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)

    r = max(0, min(255, int(r * factor)))
    g = max(0, min(255, int(g * factor)))
    b = max(0, min(255, int(b * factor)))

    return f"#{r:02x}{g:02x}{b:02x}"


def box_vertices(
    x: float,
    y: float,
    z: float,
    length: float,
    width: float,
    height: float,
) -> tuple[list[float], list[float], list[float]]:
    """
    Returns the 8 vertices of a rectangular carton.
    """

    x0, x1 = x, x + length
    y0, y1 = y, y + width
    z0, z1 = z, z + height

    xs = [x0, x1, x1, x0, x0, x1, x1, x0]
    ys = [y0, y0, y1, y1, y0, y0, y1, y1]
    zs = [z0, z0, z0, z0, z1, z1, z1, z1]

    return xs, ys, zs


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
    Creates a solid rectangular prism.

    The important fix is the triangle indexing. The old face list could make
    boxes look like incomplete folded planes from some camera angles.
    """

    xs, ys, zs = box_vertices(
        x=x,
        y=y,
        z=z,
        length=length,
        width=width,
        height=height,
    )

    # 12 triangles: 2 triangles for each of the 6 faces.
    # bottom, top, front, right, back, left.
    i = [0, 0, 4, 4, 0, 0, 1, 1, 2, 2, 3, 3]
    j = [1, 2, 5, 6, 1, 5, 2, 6, 3, 7, 0, 4]
    k = [2, 3, 6, 7, 5, 4, 6, 5, 7, 6, 4, 7]

    return go.Mesh3d(
        x=xs,
        y=ys,
        z=zs,
        i=i,
        j=j,
        k=k,
        color=color,
        opacity=opacity,
        name=name,
        hovertext=hover_text,
        hoverinfo="text",
        flatshading=True,
        lighting=dict(
            ambient=0.55,
            diffuse=0.80,
            fresnel=0.10,
            specular=0.15,
            roughness=0.65,
        ),
        lightposition=dict(x=100, y=200, z=300),
        showscale=False,
    )


def box_edges(
    x: float,
    y: float,
    z: float,
    length: float,
    width: float,
    height: float,
    color: str,
) -> go.Scatter3d:
    """
    Draws crisp edges around a carton.

    Mesh3d does not draw outlines, so adding edges makes the boxes look finished.
    """

    xs, ys, zs = box_vertices(
        x=x,
        y=y,
        z=z,
        length=length,
        width=width,
        height=height,
    )

    edge_pairs = [
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    ]

    line_x = []
    line_y = []
    line_z = []

    for a, b in edge_pairs:
        line_x.extend([xs[a], xs[b], None])
        line_y.extend([ys[a], ys[b], None])
        line_z.extend([zs[a], zs[b], None])

    return go.Scatter3d(
        x=line_x,
        y=line_y,
        z=line_z,
        mode="lines",
        line=dict(color=color, width=3),
        hoverinfo="skip",
        showlegend=False,
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
            opacity=0.55,
        )
    )

    fig.add_trace(
        box_edges(
            x=0,
            y=0,
            z=-0.35,
            length=plan.skid_length,
            width=plan.skid_width,
            height=0.35,
            color="#8b7355",
        )
    )

    color_index = 0

    for layer in plan.layers:
        for placement in layer.placements:
            color = COLORS[color_index % len(COLORS)]
            edge_color = darker_hex(color)
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

            fig.add_trace(
                box_edges(
                    x=placement.x,
                    y=placement.y,
                    z=placement.z,
                    length=placement.length,
                    width=placement.width,
                    height=placement.height,
                    color=edge_color,
                )
            )

    fig.update_layout(
        title=title,
        scene=dict(
            xaxis_title="Skid Length / X",
            yaxis_title="Skid Width / Y",
            zaxis_title="Skid Height / Z",
            xaxis=dict(
                range=[0, max(plan.skid_length, 1)],
                showbackground=True,
                backgroundcolor="#eef2f7",
                gridcolor="#ffffff",
            ),
            yaxis=dict(
                range=[0, max(plan.skid_width, 1)],
                showbackground=True,
                backgroundcolor="#eef2f7",
                gridcolor="#ffffff",
            ),
            zaxis=dict(
                range=[0, max(plan.skid_height, 1)],
                showbackground=True,
                backgroundcolor="#eef2f7",
                gridcolor="#ffffff",
            ),
            aspectmode="data",
            camera=dict(
                eye=dict(x=1.65, y=1.65, z=0.95),
                center=dict(x=0, y=0, z=0),
            ),
        ),
        margin=dict(l=0, r=0, t=50, b=0),
        height=760,
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
