# File: attributes.py

import tkinter as tk
from main import attributes_list  # Import attributes from main.py

# Global variables to persist across page loads
selected_attributes = {}  # e.g. {"PE Ratio": 25.0, "EPS": 10.0}
similarity_threshold = 0.0  # Global similarity threshold value


# Update the selected_attributes dict based on the slider value
def update_attribute(attr, value):
    value = float(value)
    if value != 0:
        selected_attributes[attr] = value
    else:
        if attr in selected_attributes:
            del selected_attributes[attr]
    # Debug print (optional)
    # print("Selected Attributes:", selected_attributes)

def update_similarity_threshold(value):
    global similarity_threshold
    similarity_threshold = float(value)


def main(content_frame):

    # Clear the content_frame
    for widget in content_frame.winfo_children():
        widget.destroy()

    # Create the sliders section that fills the content_frame
    sliders_frame = tk.Frame(content_frame, bg="white")
    sliders_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)

    # Create a canvas and a vertical scrollbar in the sliders_frame
    canvas = tk.Canvas(sliders_frame, bg="black")
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar = tk.Scrollbar(sliders_frame, orient="vertical", command=canvas.yview)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    canvas.configure(yscrollcommand=scrollbar.set)

    # Create an inner frame to hold the sliders and add it to the canvas
    inner_frame = tk.Frame(canvas, bg="black")
    inner_window = canvas.create_window((0, 0), window=inner_frame, anchor="nw")

    # Update the scroll region whenever the inner frame's size changes
    inner_frame.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
    # Ensure the inner frame's width matches the canvas width
    canvas.bind("<Configure>", lambda event: canvas.itemconfig(inner_window, width=event.width))

    # --- Add the fixed "Similarity threshold" slider at the top ---
    threshold_box = tk.Frame(inner_frame, borderwidth=2, relief="groove", padx=5, pady=5)
    threshold_box.pack(fill=tk.X, padx=10, pady=5)
    threshold_label = tk.Label(threshold_box, text="Similarity threshold", font=("Arial", 12))
    threshold_label.pack(side=tk.LEFT)
    threshold_slider = tk.Scale(
        threshold_box, from_=0, to=100, orient=tk.HORIZONTAL,
        command=update_similarity_threshold
    )
    threshold_slider.pack(fill=tk.X, expand=True, padx=10)
    # Set the slider to the stored threshold value
    threshold_slider.set(similarity_threshold)

    # --- Sliders for each attribute ---
    for attr in attributes_list:
        slider_box = tk.Frame(inner_frame, borderwidth=2, relief="groove", padx=5, pady=5)
        slider_box.pack(fill=tk.X, padx=10, pady=5)

        label = tk.Label(slider_box, text=f"{attr} Slider", font=("Arial", 12))
        label.pack(side=tk.LEFT)

        slider = tk.Scale(
            slider_box, from_=0, to=100, orient=tk.HORIZONTAL,
            command=lambda value, attr=attr: update_attribute(attr, value)
        )
        slider.pack(fill=tk.X, expand=True, padx=10)
        # Restore the slider value from selected_attributes (default to 0)
        slider.set(selected_attributes.get(attr, 0))

    # Enable mouse wheel scrolling on the canvas
    def on_mouse_wheel(event):
        canvas.yview_scroll(-1 * int(event.delta / 120), "units")
    canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", on_mouse_wheel))
    canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

if __name__ == "__main__":
    root = tk.Tk()
    root.title("Attributes Page Test")
    root.geometry("800x600")
    test_frame = tk.Frame(root, bg="gray")
    test_frame.pack(fill=tk.BOTH, expand=True)
    main(test_frame)
    root.mainloop()
