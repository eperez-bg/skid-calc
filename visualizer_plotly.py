import colorsys
import plotly.graph_objects as go


def create_box_mesh(x, y, z, length, width, height, name, color="lightblue", opacity=0.65):
    """
    Creates one rectangular 3D box as a Plotly Mesh3d object.

    x, y, z:
        The starting lower-left-bottom corner of the box.

    length:
        Size in the skid length direction, x-axis.

    width:
        Size across the skid width direction, y-axis.

    height:
        Size vertically, z-axis.
    """

    # 8 corner points of the box
    vertices = [
        (x, y, z),
        (x + length, y, z),
        (x + length, y + width, z),
        (x, y + width, z),

        (x, y, z + height),
        (x + length, y, z + height),
        (x + length, y + width, z + height),
        (x, y + width, z + height),
    ]

    xs = [point[0] for point in vertices]
    ys = [point[1] for point in vertices]
    zs = [point[2] for point in vertices]

    # 12 triangles total (2 per rectangular face)
    triangles = [
        # bottom
        (0, 1, 2), (0, 2, 3),

        # top
        (4, 5, 6), (4, 6, 7),

        # front
        (0, 1, 5), (0, 5, 4),

        # right
        (1, 2, 6), (1, 6, 5),

        # back
        (2, 3, 7), (2, 7, 6),

        # left
        (3, 0, 4), (3, 4, 7),
    ]

    i = [triangle[0] for triangle in triangles]
    j = [triangle[1] for triangle in triangles]
    k = [triangle[2] for triangle in triangles]

    return go.Mesh3d(
        x=xs,
        y=ys,
        z=zs,
        i=i,
        j=j,
        k=k,
        name=name,
        color=color,
        opacity=opacity,
        flatshading=True,
        hovertext=name,
        hoverinfo="text",
    )


def generate_distinct_colors(n):
    """
    Generate n visually distinct colors using HSV space.

    Returns a list of color strings like:
    ['rgb(255, 0, 0)', 'rgb(0, 255, 0)', ...]
    """

    colors = []

    if n <= 0:
        return colors

    for index in range(n):
        hue = index / n
        saturation = 0.70
        value = 0.95

        r, g, b = colorsys.hsv_to_rgb(hue, saturation, value)

        colors.append(
            f"rgb({int(r * 255)}, {int(g * 255)}, {int(b * 255)})"
        )

    return colors


def export_plan_to_plotly_html(plan, output_path="skid_visual.html"):
    """
    Creates an interactive 3D visualization of the skid plan.

    The output is a local HTML file.
    Open it in your browser and rotate/zoom the skid.
    """

    fig = go.Figure()

    # Draw the skid/base as a thin rectangle underneath the cartons.
    skid_base = create_box_mesh(
        x=0,
        y=0,
        z=-0.5,
        length=plan.skid_length,
        width=plan.skid_width,
        height=0.5,
        name=f"Skid base: {round(plan.skid_length, 2)} x {round(plan.skid_width, 2)}",
        color="rgb(210, 180, 140)",  # tan
        opacity=0.35,
    )

    fig.add_trace(skid_base)

    # Generate one unique color per carton
    placement_colors = generate_distinct_colors(len(plan.placements))

    # Draw every carton placement
    for index, placement in enumerate(plan.placements):
        name = (
            f"CSV row {placement.csv_row_number}, copy {placement.copy_number}<br>"
            f"Layer {placement.layer_number}<br>"
            f"Position: x={round(placement.x, 2)}, "
            f"y={round(placement.y, 2)}, "
            f"z={round(placement.z, 2)}<br>"
            f"Size: {round(placement.length, 2)} x "
            f"{round(placement.width, 2)} x "
            f"{round(placement.height, 2)}<br>"
            f"{placement.orientation}"
        )

        carton_mesh = create_box_mesh(
            x=placement.x,
            y=placement.y,
            z=placement.z,
            length=placement.length,
            width=placement.width,
            height=placement.height,
            name=name,
            color=placement_colors[index],
            opacity=0.75,
        )

        fig.add_trace(carton_mesh)

    fig.update_layout(
        title=(
            f"Skid Plan: "
            f"{round(plan.skid_length, 2)} x "
            f"{round(plan.skid_width, 2)} x "
            f"{round(plan.skid_height, 2)}"
        ),
        scene=dict(
            xaxis_title="Skid Length / X",
            yaxis_title="Skid Width / Y",
            zaxis_title="Skid Height / Z",
            aspectmode="data",
        ),
        showlegend=False,
    )

    fig.write_html(output_path, auto_open=True)
    print(f"3D visualization written to: {output_path}")