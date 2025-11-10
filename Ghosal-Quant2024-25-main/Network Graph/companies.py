# File: companies.py

import tkinter as tk
from tkinter import ttk
import pandas as pd
from main import dataframes  # Dataframes loaded from main.py
import network_graph       # Import the global variables from network_graph.py


# Helper function to create a button with consistent styling
def create_button(parent, text, command=None):
    return tk.Button(parent, text=text, font=("Arial", 12), padx=10, pady=5, command=command)

def update_ticker_list(industry_name, ticker_listbox):

    ticker_listbox.delete(0, tk.END)  # Clear the All Companies listbox
    df = dataframes.get(industry_name, None)
    if df is not None and "Ticker Symbol" in df.columns:
        network_graph.all_tickers.clear()
        for _, row in df.iterrows():
            ticker = row["Ticker Symbol"]
            if ticker:
                display_value = ticker
                if "Country Mapping" in df.columns:
                    country = row["Country Mapping"]
                    if country and str(country).strip().lower() != 'nan':
                        if str(country).startswith('.'):
                            display_value = f"{ticker}{country}"
                        else:
                            display_value = f"{ticker}.{country}"
                network_graph.all_tickers.append(display_value)
                ticker_listbox.insert(tk.END, display_value)


# Filter the 'All Companies' listbox based on the search term
def search_ticker(search_entry, ticker_listbox):
    search_term = search_entry.get().strip().lower()
    ticker_listbox.delete(0, tk.END)
    if search_term == "":
        for ticker in network_graph.all_tickers:
            ticker_listbox.insert(tk.END, ticker)
    else:
        for ticker in network_graph.all_tickers:
            if search_term in ticker.lower():
                ticker_listbox.insert(tk.END, ticker)


# Add the selected company (with suffix) from the All Companies listbox to the Selected Companies list
def add_company(ticker_listbox, selected_listbox):
    
    selected_indices = ticker_listbox.curselection()
    for index in selected_indices:
        company = ticker_listbox.get(index)
        if company not in network_graph.selected_tickers:
            network_graph.selected_tickers.append(company)
            selected_listbox.insert(tk.END, company)

# Remove the selected company from the Selected Companies list.
def remove_company(selected_listbox):
    selected_indices = selected_listbox.curselection()
    for index in reversed(selected_indices):
        company = selected_listbox.get(index)
        if company in network_graph.selected_tickers:
            network_graph.selected_tickers.remove(company)
        selected_listbox.delete(index)

# Clear all companies from the Selected Companies list
def clear_all(selected_listbox):
    network_graph.selected_tickers.clear()
    selected_listbox.delete(0, tk.END)

def main(content_frame):

    # Clear the content frame (should already be cleared by load_page)
    for widget in content_frame.winfo_children():
        widget.destroy()
    
    # Top area: Industry buttons
    industry_frame = tk.Frame(content_frame, bg="black")
    industry_frame.pack(fill=tk.X, padx=10, pady=5)
    
    industry_buttons = ["Energy", "Industrials", "Materials", "Discretionary", "Staples", "Technology"]
    for i, btn_text in enumerate(industry_buttons):
        btn = create_button(industry_frame, btn_text,
                            command=lambda name=btn_text: update_ticker_list(name, all_companies_listbox))
        btn.grid(row=0, column=i, padx=5, pady=5, sticky="ew")
        industry_frame.columnconfigure(i, weight=1)
    
    # Search bar area
    search_frame = tk.Frame(content_frame, bg="lightgray")
    search_frame.pack(fill=tk.X, padx=10, pady=5)
    
    search_entry = tk.Entry(search_frame, font=("Arial", 14), width=50)
    search_entry.pack(side=tk.LEFT, padx=10, pady=5, fill=tk.X, expand=True)
    search_button = create_button(search_frame, "Search",
                                  command=lambda: search_ticker(search_entry, all_companies_listbox))
    search_button.pack(side=tk.RIGHT, padx=10, pady=5)
    
    # Bottom area: Two listboxes side by side
    bottom_section = tk.Frame(content_frame, bg="gray")
    bottom_section.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
    bottom_section.columnconfigure(0, weight=1)
    bottom_section.columnconfigure(1, weight=1)
    bottom_section.rowconfigure(0, weight=1)
    
    # All Companies listbox
    all_companies_frame = tk.Frame(bottom_section, bg="darkgray")
    all_companies_frame.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
    
    all_companies_label = tk.Label(all_companies_frame, text="All Companies", font=("Arial", 12), bg="darkgray")
    all_companies_label.pack(pady=5)
    
    global all_companies_listbox
    all_companies_listbox = tk.Listbox(all_companies_frame, font=("Arial", 12), width=30, height=20, selectmode=tk.SINGLE)
    all_companies_listbox.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
    
    # Selected Companies listbox
    selected_companies_frame = tk.Frame(bottom_section, bg="darkgray")
    selected_companies_frame.grid(row=0, column=1, sticky="nsew", padx=5, pady=5)
    
    selected_companies_label = tk.Label(selected_companies_frame, text="Selected Companies", font=("Arial", 12), bg="darkgray")
    selected_companies_label.pack(pady=5)
    
    selected_listbox = tk.Listbox(selected_companies_frame, font=("Arial", 12), width=30, height=20, selectmode=tk.SINGLE)
    selected_listbox.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
    
    # Populate the Selected Companies listbox from the persistent global list
    for company in network_graph.selected_tickers:
        selected_listbox.insert(tk.END, company)
    
    # Bottom buttons for managing companies
    bottom_buttons_frame = tk.Frame(content_frame, bg="black")
    bottom_buttons_frame.pack(fill=tk.X, padx=10, pady=5)
    bottom_buttons_frame.columnconfigure(0, weight=1)
    bottom_buttons_frame.columnconfigure(1, weight=1)
    bottom_buttons_frame.columnconfigure(2, weight=1)
    
    add_btn = create_button(bottom_buttons_frame, "Add Company",
                            command=lambda: add_company(all_companies_listbox, selected_listbox))
    add_btn.grid(row=0, column=0, padx=5, pady=5, sticky="ew")
    
    clear_btn = create_button(bottom_buttons_frame, "Clear All",
                              command=lambda: clear_all(selected_listbox))
    clear_btn.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
    
    remove_btn = create_button(bottom_buttons_frame, "Remove Company",
                               command=lambda: remove_company(selected_listbox))
    remove_btn.grid(row=0, column=2, padx=5, pady=5, sticky="ew")

if __name__ == "__main__":
    root = tk.Tk()
    root.title("Companies Page Test")
    root.geometry("1000x600")
    main(root)
    root.mainloop()
