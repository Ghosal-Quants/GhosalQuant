# File: search_tool.py

import tkinter as tk
from tkinter import ttk
import pandas as pd

# Compute similarity for a single attribute
def compute_attribute_similarity(valA, valB, is_categorical=False):
    if valA is None or valB is None:
        return 0.0
    if is_categorical:
        return 100.0 if valA == valB else 0.0
    try:
        A = float(valA)
        B = float(valB)
        if A < 0 or B < 0:
            A, B = abs(A), abs(B)
        mx = max(A, B)
        if mx == 0:
            return 0.0
        ratio = abs(A - B) / mx
        sim = max(0, 1 - ratio) * 100
        return sim
    except (ValueError, TypeError):
        return 0.0

def filter_by_similarity_threshold(df, ticker, attribute, threshold):
    if df.empty:
        return df
    if "Ticker Symbol" not in df.columns:
        print("No 'Ticker Symbol' column found in the DataFrame.")
        return pd.DataFrame()
    
    # Find the reference row for the given ticker
    row_matches = df[df["Ticker Symbol"] == ticker]
    if row_matches.empty:
        print(f"No matching ticker found: {ticker}")
        return pd.DataFrame()
    
    ref_value = row_matches.iloc[0][attribute]
    
    # Determine if the reference value is numeric or categorical
    try:
        float(ref_value)
        ref_is_categorical = False
    except (ValueError, TypeError):
        ref_is_categorical = True

    filtered_rows = []
    for idx, row in df.iterrows():
        comp_value = row[attribute]
        try:
            float(comp_value)
            comp_is_categorical = False
        except (ValueError, TypeError):
            comp_is_categorical = True

        # If either value is categorical, treat the comparison as categorical.
        final_is_categorical = ref_is_categorical or comp_is_categorical
        # Use original values for categorical comparisons, numeric conversions otherwise.
        valA = ref_value if ref_is_categorical else float(ref_value)
        valB = comp_value if comp_is_categorical else float(comp_value)
        sim = compute_attribute_similarity(valA, valB, is_categorical=final_is_categorical)
        if sim >= threshold:
            filtered_rows.append(row)
    return pd.DataFrame(filtered_rows)

# Global variables to store the full (original) and current DataFrame being displayed.
original_df = pd.DataFrame()
current_df = pd.DataFrame()

def display_csv(industry_name, dataframes, tree, table_frame, search_widgets):
    global original_df, current_df
    df = dataframes.get(industry_name, pd.DataFrame())
    
    # Clear the tree before re-populating
    for row in tree.get_children():
        tree.delete(row)
    
    # Set new columns based on the DataFrame's columns
    tree["columns"] = list(df.columns)
    tree["displaycolumns"] = list(df.columns)
    
    # Configure column headings with fixed width and no stretching
    for col in df.columns:
        tree.heading(col, text=col)
        tree.column(col, width=150, minwidth=150, stretch=tk.NO, anchor="center")
    
    # Insert each row from the DataFrame
    for _, row_data in df.iterrows():
        tree.insert("", "end", values=list(row_data))
    
    # Prevent the table frame from resizing to its contents
    table_frame.pack_propagate(False)
    
    # Save the original and current dataframes globally
    original_df = df.copy()
    current_df = df.copy()
    
    # Update the attribute dropdown with column names
    search_entry, column_combo, threshold_entry = search_widgets
    column_combo["values"] = list(df.columns) if not df.empty else []
    if df.columns.size > 0:
        column_combo.current(0)
    search_entry.delete(0, tk.END)
    threshold_entry.delete(0, tk.END)
    threshold_entry.insert(0, "50")

def update_table(tree, df):
    for row in tree.get_children():
        tree.delete(row)
    for _, row_data in df.iterrows():
        tree.insert("", "end", values=list(row_data))

def handle_search(tree, search_widgets):
    global original_df, current_df
    search_entry, column_combo, threshold_entry = search_widgets
    ticker = search_entry.get().strip()
    attribute = column_combo.get().strip()
    
    try:
        threshold = float(threshold_entry.get().strip())
    except ValueError:
        print("Invalid threshold value. Please enter a number.")
        return
    
    if ticker == "" or attribute == "":
        return
    
    filtered_df = filter_by_similarity_threshold(original_df, ticker, attribute, threshold)
    if filtered_df.empty:
        print("No companies meet the similarity criteria.")
        return
    current_df = filtered_df.copy()
    update_table(tree, current_df)

def reset_table(tree, search_widgets):
    global original_df, current_df
    current_df = original_df.copy()
    update_table(tree, current_df)
    search_entry, column_combo, threshold_entry = search_widgets
    search_entry.delete(0, tk.END)
    if original_df.columns.size > 0:
        column_combo.current(0)
    threshold_entry.delete(0, tk.END)
    threshold_entry.insert(0, "50")



# Closes the current search tool window and opens the main title page from main.py.
def go_back(root):
    root.destroy()  # Close the current window
    from main import create_window
    create_window()  # Open the new title page window


def main(root, dataframes):
    # Clear the current window
    for widget in root.winfo_children():
        widget.destroy()
    
    root.title("Search Tool")
    root.geometry("1400x900")
    
    # --- New Section: Search Controls ---
    search_control_frame = tk.Frame(root)
    search_control_frame.pack(fill=tk.X, padx=10, pady=5)
    
    # Left side: Ticker, Attribute, Threshold inputs
    search_input_frame = tk.Frame(search_control_frame)
    search_input_frame.pack(side=tk.LEFT, padx=5)
    
    tk.Label(search_input_frame, text="Ticker:", font=("Arial", 12)).grid(row=0, column=0, padx=5, pady=5)
    search_entry = tk.Entry(search_input_frame, font=("Arial", 12), width=10)
    search_entry.grid(row=0, column=1, padx=5, pady=5)
    
    tk.Label(search_input_frame, text="Attribute:", font=("Arial", 12)).grid(row=0, column=2, padx=5, pady=5)
    column_combo = ttk.Combobox(search_input_frame, font=("Arial", 12), state="readonly", width=15)
    column_combo.grid(row=0, column=3, padx=5, pady=5)
    
    tk.Label(search_input_frame, text="Threshold:", font=("Arial", 12)).grid(row=0, column=4, padx=5, pady=5)
    threshold_entry = tk.Entry(search_input_frame, font=("Arial", 12), width=5)
    threshold_entry.grid(row=0, column=5, padx=5, pady=5)
    
    search_widgets = (search_entry, column_combo, threshold_entry)
    
    # Right side: Search, Reset, Back buttons (formatted like industry buttons)
    search_buttons_frame = tk.Frame(search_control_frame)
    search_buttons_frame.pack(side=tk.RIGHT, padx=5)
    
    search_button = tk.Button(search_buttons_frame, text="Search", font=("Arial", 12), padx=10, pady=5,
                              command=lambda: handle_search(tree, search_widgets))
    search_button.grid(row=0, column=0, padx=5, pady=5)
    
    reset_button = tk.Button(search_buttons_frame, text="Reset", font=("Arial", 12), padx=10, pady=5,
                             command=lambda: reset_table(tree, search_widgets))
    reset_button.grid(row=0, column=1, padx=5, pady=5)
    
    back_button = tk.Button(search_buttons_frame, text="Back", font=("Arial", 12), padx=10, pady=5, bg="red", fg="black",
                            command=lambda: go_back(root))
    back_button.grid(row=0, column=2, padx=5, pady=5)
    
    # --- Industry Buttons Section ---
    button_frame = tk.Frame(root, bg="black")
    button_frame.pack(fill=tk.X, padx=10, pady=5)
    
    industry_buttons = ["Energy", "Industrials", "Materials", "Discretionary", "Staples", "Technology"]
    
    # --- Table Frame with Scrollbars ---
    table_frame = tk.Frame(root)
    table_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
    
    scroll_y = ttk.Scrollbar(table_frame, orient=tk.VERTICAL)
    scroll_x = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL)
    
    tree = ttk.Treeview(table_frame, show="headings", yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
    scroll_y.config(command=tree.yview)
    scroll_x.config(command=tree.xview)
    
    tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
    scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
    
    # Create industry buttons
    for i, btn_text in enumerate(industry_buttons):
        btn = tk.Button(button_frame, text=btn_text, font=("Arial", 12), padx=10, pady=5, 
                        command=lambda name=btn_text: display_csv(name, dataframes, tree, table_frame, search_widgets))
        btn.grid(row=0, column=i, padx=5, pady=5, sticky="ew")
        button_frame.columnconfigure(i, weight=1)
    
    # Display a default CSV on startup (e.g., the first industry)
    default_industry = industry_buttons[0]
    display_csv(default_industry, dataframes, tree, table_frame, search_widgets)

if __name__ == "__main__":
    from main import dataframes 
    root = tk.Tk()
    main(root, dataframes)
    root.mainloop()
