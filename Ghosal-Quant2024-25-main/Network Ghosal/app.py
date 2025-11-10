# file: app.py

import os
import tkinter as tk
import pandas as pd

# Import the UI file
from ui import CompanySelectorApp

if __name__ == "__main__":
    # Get the current script's directory
    base_dir = os.path.dirname(os.path.abspath(__file__))  

    # Define the new data directory path
    data_dir = os.path.join(base_dir, "data")

    # Load CSV data from the new "data" folder
    df_companies = pd.read_csv(os.path.join(data_dir, "companies_list.csv"))
    df_attributes = pd.read_csv(os.path.join(data_dir, "attributes.csv"))

    # Initialize Tkinter
    root = tk.Tk()
    
    # Create and run the app
    app = CompanySelectorApp(root, df_companies, df_attributes)
    root.mainloop()