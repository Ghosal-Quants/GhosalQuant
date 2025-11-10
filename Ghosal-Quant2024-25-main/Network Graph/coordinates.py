# File: coordinates.py

import tkinter as tk
import yfinance as yf
from company import Company
from network_graph import selected_tickers  # Global list of selected companies (tickers with suffixes)
from attributes import selected_attributes  # Global dict of selected attributes (weights)

# Global cache for Company objects, keyed by ticker symbol (in uppercase)
company_cache = {}

def main(content_frame):
    # Clear the content frame
    for widget in content_frame.winfo_children():
        widget.destroy()
    
    # Create a canvas with a vertical scrollbar. Both canvas and inner frame use black.
    canvas = tk.Canvas(content_frame, bg="black")
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar = tk.Scrollbar(content_frame, orient="vertical", command=canvas.yview)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    canvas.configure(yscrollcommand=scrollbar.set)
    
    # Create an inner frame in the canvas for the coordinates display.
    inner_frame = tk.Frame(canvas, bg="black")
    canvas.create_window((0, 0), window=inner_frame, anchor="nw")
    
    # Update the scroll region when inner_frame size changes.
    inner_frame.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
    
    # Check if there are selected companies and attributes.
    if not selected_tickers:
        tk.Label(inner_frame, text="No companies selected.", font=("Arial", 14),
                 bg="black", fg="white").pack(pady=10)
        return
    if not selected_attributes:
        tk.Label(inner_frame, text="No attributes selected.", font=("Arial", 14),
                 bg="black", fg="white").pack(pady=10)
        return

    # Get the list of attribute names from the selected_attributes dictionary.
    attributes_keys = list(selected_attributes.keys())
    
    # Create a list to hold Company objects for each selected ticker, using the cache.
    companies = []
    for ticker in selected_tickers:
        ticker_upper = ticker.upper()
        if ticker_upper in company_cache:
            comp = company_cache[ticker_upper]
        else:
            comp = Company(ticker_upper, attributes_keys)
            company_cache[ticker_upper] = comp
        companies.append(comp)
    
    # Display the information for each company.
    for comp in companies:
        # Retrieve company name using yfinance as per the provided helper code.
        stock = yf.Ticker(comp.ticker)
        company_name = stock.info.get("longName", stock.info.get("shortName", comp.ticker))
        
        # Header now displays the company name.
        header = tk.Label(inner_frame, text=f"Company: {company_name}", 
                          font=("Arial", 16, "bold"), bg="black", fg="white")
        header.pack(anchor="w", pady=(10, 0), padx=10)
        
        # First line displays the ticker.
        ticker_line = tk.Label(inner_frame, text=f"Ticker: {comp.ticker}", 
                               font=("Arial", 14), bg="black", fg="white")
        ticker_line.pack(anchor="w", padx=30)
        
        # Display the remaining attributes and their weights.
        for attr in attributes_keys:
            # Optionally skip "ticker" if it appears among selected attributes.
            if attr.lower() == "ticker":
                continue
            value = getattr(comp, attr, "N/A")
            weight = selected_attributes.get(attr, 0)
            line = tk.Label(inner_frame, text=f"{attr}: {value}, {weight}", 
                            font=("Arial", 14), bg="black", fg="white")
            line.pack(anchor="w", padx=30)
    
if __name__ == "__main__":
    # Standalone test: Create a temporary window with a black background.
    root = tk.Tk()
    root.title("Coordinates Page Test")
    root.geometry("800x600")
    test_frame = tk.Frame(root, bg="black")
    test_frame.pack(fill=tk.BOTH, expand=True)
    main(test_frame)
    root.mainloop()
