import requests
import pandas as pd
import re
from datetime import datetime, timedelta
import yfinance as yf
import pytz
from typing import Dict, List, Optional, Union
from dataclasses import dataclass
import warnings
import time

warnings.simplefilter(action="ignore", category=FutureWarning)

@dataclass
class PortfolioPosition:
    units: float
    totalCost: float

class StockDataFetcher:
    def __init__(self, apiUrls: List[str]):
        self.apiUrls = apiUrls
        self.nyTimezone = pytz.timezone('America/New_York')

    def fetchRawData(self) -> Optional[pd.DataFrame]:
        """Fetch raw data from Google Sheets API."""
        for url in self.apiUrls:
            for attempt in range(5):  # Retry up to 5 times
                try:
                    response = requests.get(url)
                    print(f"Attempt {attempt + 1} for URL {url}")
                    if response.status_code == 200:
                        data = response.json()
                        df = pd.DataFrame(data)
                        df.columns = df.iloc[0]
                        df = df.drop(0).reset_index(drop=True)
                        return df
                    elif response.status_code in [403, 404]:
                        break   
                except requests.RequestException as e:
                    print(f"Attempt {attempt + 1} failed for URL {url}: {e}")
                    time.sleep(1)  
        print("Failed to fetch data from all provided URLs")
        return None

    @staticmethod
    def cleanNumericValue(value: Union[str, float, None]) -> float:
        """Clean numeric values by removing currency symbols and commas."""
        try:
            if pd.isna(value) or value == '':
                return 0.0
            if isinstance(value, (int, float)):
                return float(value)
            # Updated regex to handle £ and other currency symbols
            cleaned = re.sub(r'[£$,\s]', '', str(value))
            return float(cleaned)
        except ValueError as e:
            print(f"Error cleaning value '{value}': {e}")
            return 0.0

    @staticmethod
    def extractTicker(holding: str) -> str:
        """Extract ticker symbol from holding name."""
        match = re.search(r'\(([^)]+)\)', holding)
        return match.group(1) if match else holding

    def processRawData(self, df: pd.DataFrame) -> pd.DataFrame:
        """Process and clean the raw dataframe."""
        try:
            df['Holding'] = df['Holding'].apply(self.extractTicker)
            df['Units'] = df['Units'].apply(self.cleanNumericValue)
            df['Cost per Unit'] = df['Cost per Unit'].apply(self.cleanNumericValue)
            df['Total Cost'] = df['Total Cost'].apply(self.cleanNumericValue)
            df["Currency"] = df["Currency"]
            df['Sector'] = df['Sector'] 
            df['Country'] = df['Country']
            df['Holding Name'] = df['Holding Name']
            
            # standardize datetime, avoids annoying new date format (ISO 8601)
            df['Date'] = df['Date'].str.slice(0, 10)
            df['Date'] = pd.to_datetime(df['Date'], format='%Y-%m-%d')
            
            today = datetime.now().date()
            df = df[df['Date'].dt.date <= today]
            
            return df
        
        except Exception as e:
            print(f"Error processing data: {e}")
            return pd.DataFrame()

class PortfolioTracker:
    def __init__(self, ):
        self.nyTimezone = pytz.timezone('America/New_York')
        self.exchangeRates = {"GBP": 1.0, "GBp": 0.01}
        self.fetchExchangeRates()
        
    def fetchExchangeRates(self):
        """Fetch the latest exchange rates using yfinance."""
        currencies = [
    'USDGBP=X',  # US Dollar
    'EURGBP=X',  # Euro
    'NOKGBP=X',  # Norwegian Krone
    'IDRGBP=X',  # Indonesian Rupiah
    'JPYGBP=X',  # Japanese Yen
    'CNYGBP=X',  # Chinese Yuan
    'AUDGBP=X',  # Australian Dollar
    'CADGBP=X',  # Canadian Dollar
    'CHFGBP=X',  # Swiss Franc
    'SEKGBP=X',  # Swedish Krona
    'NZDGBP=X',  # New Zealand Dollar
    'SGDGBP=X',  # Singapore Dollar
    'INRGBP=X',  # Indian Rupee
    'ZARGBP=X',  # South African Rand
    'MXNGBP=X',  # Mexican Peso
    'KRWGBP=X',  # South Korean Won
    'HKDGBP=X',  # Hong Kong Dollar
    'BRLGBP=X',  # Brazilian Real
    'PLNGBP=X',  # Polish Zloty
    'TRYGBP=X'   # Turkish Lira
]
        try:
            for currency in currencies:
                ticker = yf.Ticker(currency)
                price = ticker.history(period='1d')['Close'][-1]
                symbol = currency[:3]
                self.exchangeRates[symbol] = float(price)
        except Exception as e:
            print(f"Error fetching exchange rates: {e}") 
        
    def getHistoricalData(self, ticker: str, startDate: datetime, endDate: datetime) -> pd.Series:
        """Fetch historical price data for a ticker."""
        try:
            startUtc = pd.Timestamp(startDate).tz_localize(None)
            endUtc = pd.Timestamp(endDate).tz_localize(None)

            if endUtc > datetime.now():
                endUtc = datetime.now().tz_localize(None)

            print(f"Fetching historical data for {ticker} from {startUtc} to {endUtc}")
            
            stock = yf.Ticker(ticker)

            # daily historical data
            history = stock.history(
                start=startUtc,
                end=endUtc + timedelta(days=1),
                interval='1d'

            )
            
            history.index = history.index.tz_localize(None)
            
            # TODO: Perhaps add warning for future dates instead of cutting off?
            
            return history['Close']
            
        except Exception as e:
            print(f"Error fetching historical data for {ticker}: {e}")
            return pd.Series(dtype='float64')

    def calculateDailyValue(self, historical: pd.Series, date: datetime, units: float, currency: str) -> float:
        """Calculate the value of a position for a specific date and convert to the appropriate currency."""
        try:
            if historical.empty:
                return 0.0

            dateNaive = pd.Timestamp(date).tz_localize(None)
            
            # Try to get the exact date's price
            if dateNaive in historical.index:
                value = historical[dateNaive] 
            else:
                # Get the most recent previous price
                priorDates = historical.index[historical.index <= dateNaive]
                if len(priorDates) > 0:
                    mostRecentDate = priorDates[-1]
                    value = historical[mostRecentDate] 
                else:
                    value = 0.0
            
            # Convert value to the appropriate currency
            exchangeRate = self.exchangeRates.get(currency, float("-inf"))
            if exchangeRate == float("-inf"):
                print(f"Error: Exchange rate for {currency} not found.")
                return 0.0
                        
            return (value * exchangeRate) * units
        except Exception as e:
            print(f"Error calculating daily value: {e}")
            return 0.0

    def trackPortfolio(self, df: pd.DataFrame) -> pd.DataFrame:
        """Track portfolio value day by day."""
        if df.empty:
            return pd.DataFrame()

        startDate = df['Date'].min()
        endDate = datetime.now().date()
        
        # Create timezone-naive date range
        dateRange = pd.date_range(start=startDate, end=endDate, tz=None)
        
        portfolio: Dict[str, PortfolioPosition] = {}
        results: List[Dict] = []
        
        # Pre-fetch all historical data
        historicalData = {
            holding: self.getHistoricalData(holding, startDate, endDate)
            for holding in df['Holding'].unique()
        }
        
        for date in dateRange:
            dateNormalized = date
            
            dayTransactions = df[df['Date'] == date]
            
            # Process day's transactions
            for _, transaction in dayTransactions.iterrows():
                holding = transaction['Holding']
                currency = transaction['Currency']
                if holding not in portfolio:
                    portfolio[holding] = PortfolioPosition(0.0, 0.0)
                    initialDate = transaction['Date'] - timedelta(days=1)
                    results.append({
                    'Date': initialDate,
                    'Holding': holding,
                    'Units': 0,
                    'Initial Value': 0,
                    'Current Value': 0,
                    'PercentageDiff': 0,
                    'Country': transaction['Country'],
                    'Holding Name': transaction['Holding Name']
                })
                
                units = transaction['Units']
                totalCost = transaction['Total Cost']
                #totalCost *= self.exchangeRates.get(currency, float("-inf")) # if not currency then it will be obvious
                transactionType = transaction['Type']
                
                if transactionType in ['Purchase']:
                    portfolio[holding].units += units
                    portfolio[holding].totalCost += totalCost
                elif transactionType in ['CFD Fee', 'Trade Fee']:
                    portfolio[holding].totalCost -= totalCost
                elif transactionType in ['Sale']:
                    if portfolio[holding].units >= units:
                        # Calculate the average cost per unit
                        avg_cost_per_unit = portfolio[holding].totalCost / portfolio[holding].units

                        # Compute the cost reduction based on units sold
                        cost_reduction = avg_cost_per_unit * units

                        # Update portfolio holdings
                        portfolio[holding].units -= units
                        portfolio[holding].totalCost -= cost_reduction  # Adjust total cost proportionally

                        # Ensure total cost is never negative (handles edge cases)
                        if portfolio[holding].units == 0:
                            portfolio[holding].totalCost = 0
                    else:
                        print(
                            f"Warning: Attempted to sell {units} units of {holding} but only {portfolio[holding].units} are held.")
                else:
                    print(f"Unknown transaction type: {transactionType}")
                    
            
            # Calculate daily positions
            totalValue = 0.0
            totalCost = 0.0
            
            sectorTotals = {}
            
            for holding, position in portfolio.items():
                if position.units > 0: # Only calculate for active positions
                    currency = df[df['Holding'] == holding]['Currency'].values[0]
                    value = self.calculateDailyValue(historicalData[holding], date, position.units, currency) 
                    totalValue += value 
                    totalCost += position.totalCost
                    sector = df[df['Holding'] == holding]['Sector'].iloc[0]
                    
                    if sector == 'Tech':
                        sector = 'Technology'
                        
                    elif sector == 'Discretionary':
                        sector = 'Consumer Discretionary'
                        
                    elif sector == 'Staples':
                        sector = 'Consumer Staples'

                    if sector not in sectorTotals:
                        sectorTotals[sector] = [0, 0]
                        sectorTotals[sector][0] += value 
                        sectorTotals[sector][1] += position.totalCost 
                        
                    else:
                        sectorTotals[sector][0] += value 
                        sectorTotals[sector][1] += position.totalCost
                                                            
                    results.append({
                        'Date': dateNormalized,
                        'Holding': holding,
                        'Units': position.units,
                        'Initial Value': position.totalCost,
                        'Current Value': value,
                        'PercentageDiff': ((value - position.totalCost) / position.totalCost * 100) if position.totalCost != 0 else 0,
                        'Country': df[df['Holding'] == holding]['Country'].iloc[0],
                        'Holding Name': df[df['Holding'] == holding]['Holding Name'].iloc[0]
                    })
            
            # Add portfolio total
            if totalCost != 0:
                results.append({
                    'Date': dateNormalized,
                    'Holding': 'TOTALPORTFOLIO',
                    'Units': 0,
                    'Initial Value': totalCost,
                    'Current Value': totalValue,
                    'PercentageDiff': ((totalValue - totalCost) / totalCost * 100)
                })
                
            for sector, (value, cost) in sectorTotals.items():
                results.append({
                    'Date': dateNormalized,
                    'Holding': f'SECTOR_{sector}',
                    'Units': 0,
                    'Initial Value': cost,
                    'Current Value': value,
                    'PercentageDiff': ((value - cost) / cost * 100) if cost != 0 else 0,
                    'Sector': sector
                })
        
        return pd.DataFrame(results)

def main(sheetsUrl: str) -> Optional[pd.DataFrame]:
    """Main function to fetch and process portfolio data."""
    fetcher = StockDataFetcher(sheetsUrl)
    rawData = fetcher.fetchRawData()
    
    if rawData is None:
        return None
    
    if rawData.columns[1] == 'POTW':
        return rawData

    processedData = fetcher.processRawData(rawData)
    
    if processedData.empty:
        return None
        
    tracker = PortfolioTracker()
    return tracker.trackPortfolio(processedData)

