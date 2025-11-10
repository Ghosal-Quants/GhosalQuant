import streamlit as st
import pandas as pd
import numpy as np
import os
import yfinance as yf
import time
import datetime as dt
from scipy import stats
from typing import Dict, List, Tuple, Optional
from refactoredSheetdata import main as fetchSheetData
    

def checkCacheFolder():
    if not os.path.exists("dashboard/cacheFolder"):
        os.mkdir("dashboard/cacheFolder")
    return

def getCachedData(cacheName: str) -> pd.DataFrame:

    sheetUrls = {
        "activeData": ["https://script.google.com/macros/s/AKfycbydIk0do9pjnIhZsygXPuPx6DkTfHD8rzLWFaydlyIIkFfIgP0oR-jkhLMyC1Q9DmsE_A/exec", "https://script.google.com/macros/s/AKfycbw44rjBxCsoPLmdUsGRCoxHdS7NmSNBHCzteTsgGMyIcQy2RBQnxRiM_Z3C9idyJweK_Q/exec"],
        "passiveData": ["https://script.google.com/macros/s/AKfycby4qHeclOgYyrYSlGhazEraCPuJA7xnt4g2MGn6-9ER1VG19PmzBrXkLtHK7GhZZJSW/exec", "https://script.google.com/macros/s/AKfycbw7BLDekvTW3qntgsA8d1gQLTM4rqah9eX9yK32-SMMiSIEH9ZF-OAyzYokog50ZQ/exec"]
    }

    validCachingTypes = ["benchmarkData", "activeData", "passiveData"] + [f"sectorBenchmark_{sector}" for sector in SECTOR_BENCHMARKS.keys()]

    if cacheName not in validCachingTypes:
        raise ValueError(f"Invalid cache type: {cacheName}. Valid types are: {validCachingTypes}")
    
    try:
        checkCacheFolder()
        cacheString = f'{cacheName}{dt.date.today().strftime("%Y-%m-%d")}.csv'
        folderPath = "./dashboard/cacheFolder"
        filePath = f"{folderPath}/{cacheString}"
        
        if os.path.exists(filePath):
            return pd.read_csv(filePath)

        for file in os.listdir(folderPath):
            if file.startswith(cacheName) and file != cacheString:
                os.remove(f"{folderPath}/{file}")

        # if benchmark then we already have it from yfinance, otherwise fetch from sheet
        if cacheName == "benchmarkData":
            df = getBenchmarkData(start="2024-11-06", timePeriod="1d")
            df.to_csv(filePath, index=False)
            return df
        
        
        else:
            fetchedData = fetchSheetData(sheetUrls[cacheName])
            fetchedData.to_csv(filePath, index=False)
            return fetchedData

    except Exception as e:
        raise RuntimeError(f"Error reading cached data: {e}")
    
def clearCache(cacheName: str) -> pd.DataFrame:
   
    validCachingTypes = ["benchmarkData", "activeData", "passiveData"] + [f"sectorBenchmark_{sector}" for sector in SECTOR_BENCHMARKS.keys()]

    if cacheName not in validCachingTypes:
        raise ValueError(f"Invalid cache type: {cacheName}. Valid types are: {validCachingTypes}")
    
    try:
        checkCacheFolder()
        cacheString = f'{cacheName}{dt.date.today().strftime("%Y-%m-%d")}.csv'
        folderPath = "./dashboard/cacheFolder"
        filePath = f"{folderPath}/{cacheString}"
        
        if os.path.exists(filePath):
            os.remove(filePath)

        return getCachedData(cacheName)

    except Exception as e:
        raise RuntimeError(f"Error clearing cache: {e}")
    

def getPitchesOfTheWeek() -> pd.DataFrame:
    fetchedPitches = fetchSheetData([
        "https://script.google.com/macros/s/AKfycbzYs8m0NzrCm9ojQ3y0WPFL_SScgSJHC6F7N6OA2hBIRckFDAGYhgZcY_RMmJIXN_yB7w/exec",
        "https://script.google.com/macros/s/AKfycbx3gtSS8k-sUqXl4FtfsXfRkaSBpGgZXHiVSVoKgSAyut0aqZBd19J3caHMrmx6aAiZRg/exec"
    ])

    # Fetch passive data
    passiveDataDf = getCachedData(cacheName="passiveData")

    # Initialize percentage return list with same length as fetchedPitches
    percentage_returns = [None] * len(fetchedPitches)

    for i, stock in enumerate(fetchedPitches['POTW']):
        filtered_data = passiveDataDf[passiveDataDf['Holding Name'] == stock].sort_values(by='Date')

        # Check if data exists before accessing .iloc[-1]
        if not filtered_data.empty:
            recent_data = filtered_data.iloc[-1]
            percentage_returns[i] = round(recent_data['PercentageDiff'], 2)
        else:
            print(f"Warning: No data found for stock {stock}")

    # Assign the column with the corrected length
    fetchedPitches['Percentage Return (%)'] = percentage_returns

    print(fetchedPitches)
    return fetchedPitches

    
def getBenchmarkData(start: str, timePeriod: str) -> pd.DataFrame:
    '''Fetch MSCI ACWI index data from Yahoo Finance
       Defaults to a 1-year period from today if no start date is provided.'''
    msciAcwi = yf.Ticker("ACWI")
    # timePeriods = ["1d", "5d", "1mo"]
    # timePeriod = timePeriods[0] # fix this to parameterize

    if not start:
        start = (dt.datetime.now() - dt.timedelta(days=365)).strftime('%Y-%m-%d')

    df = msciAcwi.history(start=start, end=dt.datetime.today().strftime('%Y-%m-%d'), interval="1d")

    # Note - I've made a decision to drop everything, compute percentage diff then cache, since data is not used for anything else at the minute
    df.drop(['Open', 'High', 'Low', 'Volume', 'Dividends', 'Stock Splits', 'Capital Gains'], axis=1, inplace=True)
    df["Date"] = df.index
    df.reset_index(drop=True, inplace=True)
    df['Date'] = pd.to_datetime(df['Date']).dt.date
    df["PercentageDiff"] = (df["Close"] / df["Close"].iloc[0] - 1) * 100

    return df

def computeStatistics(portfolio: pd.DataFrame, benchmark: pd.DataFrame) -> dict:
    '''Compute performance metrics for the portfolio'''

    # Filter data to only include dates where we have benchmark data & normalize
    # NOTE: Sample size is fairly small, it's going to skew the results, surely?
    portfolio = portfolio[portfolio['Holding'] == 'TOTALPORTFOLIO'].copy()
    portfolio = portfolio[portfolio["Date"].isin(benchmark["Date"])]
    portfolio["PercentageDiff"] -= portfolio["PercentageDiff"].iloc[0]

    dataReturns = portfolio["PercentageDiff"] / 100
    benchmarkReturns = benchmark["PercentageDiff"] / 100

    min_length = min(len(dataReturns), len(benchmarkReturns))
    benchmarkReturns = benchmarkReturns.iloc[:min_length].reset_index(drop=True)
    dataReturns = dataReturns.iloc[:min_length].reset_index(drop=True)
    riskFreeRate = 0.043

    # Note - using number of trading days in benchmark for now, as portfolio is too new to use full year (252 trading days) and will skew data
    tradingDays = min(len(benchmark["Date"]), 252) # this has the added benefit of being dynamic & being actual trading days
    volatility = dataReturns.std() * np.sqrt(tradingDays) # Annualized vol
    sharpeRatio = (dataReturns.mean() * tradingDays - riskFreeRate) / volatility

    beta, alpha, rValue, pValue, std_err = stats.linregress(benchmarkReturns, dataReturns)
    rSquared = rValue ** 2

    # Compute max drawdown
    cumulativeReturns = (1 + dataReturns).cumprod()
    rollingMax = cumulativeReturns.cummax()
    drawdown = (cumulativeReturns - rollingMax) / rollingMax
    maxDrawdown = drawdown.min()

    # Return the computed metrics
    computedData = [
        {"Ratio": "Volatility", "Value": round(volatility, 2)},
        {"Ratio": "Alpha", "Value": round(alpha, 2)},
        {"Ratio": "Beta", "Value": round(beta, 2)},
        {"Ratio": "Sharpe Ratio", "Value": round(sharpeRatio, 2)},
        {"Ratio": "R Squared", "Value": round(rSquared, 2)},
        {"Ratio": "Max Drawdown", "Value": round(maxDrawdown, 2)}
    ]
    return computedData

def computePortfolioDistribution(data: pd.DataFrame) -> pd.DataFrame:
    '''Compute the distriubtion of equities in the portfolio'''
    filteredData = data[(data["Date"] == dt.datetime.today().strftime('%Y-%m-%d')) & (data["Holding"] != "TOTALPORTFOLIO") & (~data["Holding"].str.contains("SECTOR"))]
    totalValue = filteredData["Current Value"].sum()
    filteredData["Percentage (%)"] = (filteredData["Current Value"] / totalValue) * 100
    filteredData["Percentage (%)"] = filteredData["Percentage (%)"].round(2).astype(str).str.rstrip('0').str.rstrip('.')  
    sortedData = filteredData.sort_values(by="Percentage (%)", ascending=False)
    result = sortedData[["Holding Name", "Percentage (%)"]].reset_index(drop=True).head(10)

    result.index = range(1, len(result) + 1)

    return result


def getTotalPortfolioData(df: pd.DataFrame) -> pd.DataFrame:
    # Performance for total portfolio - from start of portfolio to today
    return df[df['Holding'] == 'TOTALPORTFOLIO']

SECTOR_BENCHMARKS = {
    "Materials": "WMTS.AS",
    "Technology": "WITS.AS",
    "Consumer Staples": "WCSS.AS",
    "Industrials": "WINS.AS",
    "Energy": "WENS.AS",
    "Consumer Discretionary": "WCDS.AS"
}
 
def getSectorBenchmarkData(sector: str, start: str = None) -> pd.DataFrame:
    """Fetch and cache sector benchmark data"""
    if sector not in SECTOR_BENCHMARKS:
        raise ValueError(f"Invalid sector: {sector}")
    
    cacheName = f"sectorBenchmark_{sector}"
    cacheString = f'{cacheName}{dt.date.today().strftime("%Y-%m-%d")}.csv'
    folderPath = "./dashboard/cacheFolder"
    filePath = f"{folderPath}/{cacheString}"
    
    if os.path.exists(filePath):
        return pd.read_csv(filePath)

    ticker = yf.Ticker(SECTOR_BENCHMARKS[sector])
    
        
    df = ticker.history(start=start, end=dt.datetime.today().strftime('%Y-%m-%d'), interval="1d")

    df.drop(['Open', 'High', 'Low', 'Volume', 'Dividends', 'Stock Splits', 'Capital Gains'], axis=1, inplace=True)
    df["Date"] = df.index
    df.reset_index(drop=True, inplace=True)
    df['Date'] = pd.to_datetime(df['Date']).dt.date
    df["PercentageDiff"] = (df["Close"] / df["Close"].iloc[0] - 1) * 100
    df.to_csv(filePath, index=False)
    return df

def getSectorPerformance(df: pd.DataFrame, sector: str, benchmarkData: pd.DataFrame, isPassive=False) -> pd.DataFrame:
    # Performance for sectors - from start of portfolio to today
    sector_key = f'SECTOR_{sector}'
    sector_data = df[df['Holding'] == sector_key].copy()  
    sector_data = sector_data.rename(columns={'PercentageDiff': sector})  
    
    if not isPassive:
        date = "2024-11-07"
    elif isPassive:
        if sector == "Technology" or sector == "Consumer Discretionary" or sector == "Industrials":
            date = "2024-11-07"
        else: 
            date = "2024-11-14"
            
    initial_row = pd.DataFrame({
        'Date': [pd.to_datetime(date).strftime('%Y-%m-%d')],
        sector: [0],
        f'Benchmark (iShares Global {sector})': [0]
    })
            

    sectorBenchmark = getSectorBenchmarkData(sector, start=(pd.to_datetime(date) - pd.Timedelta(hours=48)).strftime('%Y-%m-%d'))
    sector_data = pd.merge(sector_data, sectorBenchmark[['Date', 'PercentageDiff']], how='inner', on='Date')
    sector_data = sector_data.rename(columns={'PercentageDiff': f'Benchmark (iShares Global {sector})'})
    sector_data = pd.concat([initial_row, sector_data], ignore_index=True)

    return sector_data

def getSectorSplit(df: pd.DataFrame, sector: str) -> float:
    # Get the current sector split
    sector_key = f'SECTOR_{sector}'
    sector_value = df[df['Holding'] == sector_key].iloc[-1]['Current Value']
    total_value = df[df['Holding'] == 'TOTALPORTFOLIO'].iloc[-1]['Current Value']
    return round((sector_value / total_value) * 100, 1)

def getSectorPerformanceToday(df: pd.DataFrame, sector: str) -> float:
    # Get the sector performance for today
    sector_key = f'SECTOR_{sector}'
    sector_data = df[df['Holding'] == sector_key].copy()
    return sector_data.iloc[-2]['PercentageDiff']

def getStocks(df: pd.DataFrame) -> pd.DataFrame:
    # Get just the stocks in the portfolio
    return df[
        (df["Holding"] != "TOTALPORTFOLIO") & 
        (~df["Holding"].str.contains("SECTOR"))  & 
        (df["Current Value"] > 10) & 
        (df["PercentageDiff"] != -100)
        # &(df["Holding"].isin(df[df["Date"] == df["Date"].max()]["Holding"].unique()))
    ]     
   

def getGeographicSplit(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate percentage of portfolio value in each country"""

    # Filter for the latest available date per stock
    latest_date = df["Date"].max()
    stocksData = df[
        (df["Date"] == latest_date) &
        (df["Holding"] != "TOTALPORTFOLIO") &
        (~df["Holding"].str.contains("SECTOR"))
        ]

    # Group by country and sum the Current Value of stocks
    geographicSplit = (
        stocksData.groupby("Country")["Current Value"]
        .sum()
        .reset_index(name='Total Value')
    )

    # Calculate percentages
    totalPortfolioValue = geographicSplit['Total Value'].sum()
    geographicSplit['Percentage'] = round((geographicSplit['Total Value'] / totalPortfolioValue) * 100, 1)

    # Sort by percentage descending
    geographicSplit = geographicSplit.sort_values('Percentage', ascending=False)

    return geographicSplit[["Country", "Percentage"]]


def getPortfolioPercentageDiffOverTime(df: pd.DataFrame) -> pd.DataFrame:
    # Get the total portfolio returns over time
    return getTotalPortfolioData(df)[['Date', 'PercentageDiff']]

def getPortfolioReturns(df: pd.DataFrame) -> pd.DataFrame:
    # Get the total portfolio data for today
    total_portfolio_df = df[df['Holding'] == 'TOTALPORTFOLIO']
    total_portfolio_df = total_portfolio_df.sort_values(by='Date')
    latest_total_portfolio = total_portfolio_df.iloc[-1]
    
    # return percentage return, current market value, and total invested (including fees and costs)
    result = {
        'Date': latest_total_portfolio['Date'],
        'PercentageDiff': latest_total_portfolio['PercentageDiff'],
        'Value': latest_total_portfolio['Current Value'],
        'TotalCost': latest_total_portfolio['Initial Value']
    }
    return pd.DataFrame([result])

def getWeeklyData(df: pd.DataFrame, start_date: str) -> pd.DataFrame:
    """Filter the dataframe to keep every 5th row for weekly data starting from a specific date"""
    weeklyData = df.copy()
    weeklyData['Date'] = pd.to_datetime(weeklyData['Date'])
    weeklyData = weeklyData.sort_values('Date')
    
        
    # Filter to start from the specified date
    start_date = pd.to_datetime(start_date)
    weeklyData = weeklyData[weeklyData['Date'] >= start_date]
    
    # Initialize list to store filtered rows
    filtered_rows = []
    previous_monday = None
    
    # iterate over df
    for i in range(len(weeklyData)):
        current_date = weeklyData.iloc[i]['Date']
        if current_date.strftime("%A") == "Monday":
            if previous_monday is not None:
                row = weeklyData.iloc[i].copy()
                row['Benchmark (MSCI ACWI)'] = weeklyData.iloc[i]['Benchmark (MSCI ACWI)'] - previous_monday['Benchmark (MSCI ACWI)']
                row['Benchmark (MSCI ACWI) Passive'] = weeklyData.iloc[i]['Benchmark (MSCI ACWI) Passive'] - previous_monday['Benchmark (MSCI ACWI) Passive']
                row['Ghosal Active Fund'] = weeklyData.iloc[i]['Ghosal Active Fund'] - previous_monday['Ghosal Active Fund']
                row['Ghosal Passive Fund'] = weeklyData.iloc[i]['Ghosal Passive Fund'] - previous_monday['Ghosal Passive Fund']
                filtered_rows.append(row)       
            else: 
                filtered_rows.append(weeklyData.iloc[i])    
            previous_monday = weeklyData.iloc[i]
    
    # Convert list back to dataframe
    weeklyData = pd.DataFrame(filtered_rows).reset_index(drop=True)
    
    weeklyData.to_csv("weeklyData.csv", index=False)

    return weeklyData

def loadData() -> Tuple[float, float, pd.DataFrame, pd.DataFrame]:
    activeDataDf = getCachedData(cacheName="activeData") 
    passiveDataDf = getCachedData(cacheName="passiveData")
    benchmarkData = getCachedData(cacheName="benchmarkData")
    
    # Convert Date columns to datetime
    activeDataDf['Date'] = pd.to_datetime(activeDataDf['Date'])
    passiveDataDf['Date'] = pd.to_datetime(passiveDataDf['Date']) 
    benchmarkData['Date'] = pd.to_datetime(benchmarkData['Date'])

    activePerformance = activeDataDf[activeDataDf['Holding'] == 'TOTALPORTFOLIO'].iloc[-2]['PercentageDiff']
    passivePerformance = passiveDataDf[passiveDataDf['Holding'] == 'TOTALPORTFOLIO'].iloc[-2]['PercentageDiff']

    # Get portfolio data
    activePortfolioData = getTotalPortfolioData(activeDataDf)
    passivePortfolioData = getTotalPortfolioData(passiveDataDf)
    mergedData = pd.merge(activePortfolioData, passivePortfolioData, how='left', on='Date', suffixes=('_Active', '_Passive'))
    
    # Merge portfolio data with benchmark data
    portfolioComparisons = pd.merge(mergedData, benchmarkData, how='inner', on='Date')
    
    # Add row for start date
    first_row = portfolioComparisons.iloc[0].copy()
    first_row['PercentageDiff_Active'] = 0
    first_row['PercentageDiff'] = 0
    first_row['Date'] = pd.to_datetime("2024-11-07")
    portfolioComparisons = pd.concat([pd.DataFrame([first_row]), portfolioComparisons], ignore_index=True)
    
    portfolioComparisons.rename(columns={"PercentageDiff_Active": "Ghosal Active Fund", 
                                       "PercentageDiff_Passive": "Ghosal Passive Fund", 
                                       "PercentageDiff": "Benchmark (MSCI ACWI)"}, inplace=True)
    
        # Create a new column for the benchmark for passive
    portfolioComparisons['Benchmark (MSCI ACWI) Passive'] = portfolioComparisons['Benchmark (MSCI ACWI)']
    
    # Reset the benchmark for passive starting from 2024-11-07
    start_date = pd.to_datetime("2024-11-07")
    mask = portfolioComparisons['Date'] >= start_date
    portfolioComparisons.loc[mask, 'Benchmark (MSCI ACWI) Passive'] -= portfolioComparisons.loc[mask, 'Benchmark (MSCI ACWI) Passive'].iloc[0]
    
    portfolioComparisons.loc[~mask, 'Benchmark (MSCI ACWI) Passive'] = np.nan

    portfolioComparisons.loc[portfolioComparisons['Date'] == pd.to_datetime("2024-11-07"), 'Ghosal Passive Fund'] = 0
    
    # Get weekly comparisons with different start dates
    activeWeeklyComparisons = getWeeklyData(portfolioComparisons, start_date="2024-11-11")
    passiveWeeklyComparisons = getWeeklyData(portfolioComparisons, start_date="2024-11-11")    
    
    return activePerformance, passivePerformance, portfolioComparisons, activeWeeklyComparisons, passiveWeeklyComparisons

def computeBenchmarksPerformances() -> Tuple[float, Dict[str, float]]:
    """
    Compute and return key benchmark performance metrics:
      - benchmarkPerformance: MSCI ACWI's second-to-last PercentageDiff
      - sectorBenchmarkPerformances: a dictionary mapping each sector to its second-to-last PercentageDiff
    """
    # Fetch benchmark data from cache
    benchmarkData = getCachedData(cacheName="benchmarkData")
    benchmarkData["Date"] = pd.to_datetime(benchmarkData["Date"])
    benchmarkPerformance = benchmarkData.iloc[-2]["PercentageDiff"]

    # Compute sector benchmark performances
    sectorBenchmarkPerformances = {}
    for sector in SECTOR_BENCHMARKS.keys():
        
        date = "2024-11-14"    
        
        if sector == "Technology" or sector == "Consumer Discretionary" or sector == "Industrials":
            date = "2024-11-07"
            
        sectorDf = getSectorBenchmarkData(sector, start=(pd.to_datetime(date) - pd.Timedelta(hours=48)).strftime('%Y-%m-%d'))
        sectorDf["Date"] = pd.to_datetime(sectorDf["Date"])
        sectorPerformance = sectorDf.iloc[-2]["PercentageDiff"]
        sectorBenchmarkPerformances[sector] = sectorPerformance

    return benchmarkPerformance, sectorBenchmarkPerformances
