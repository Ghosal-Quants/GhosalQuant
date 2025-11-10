import streamlit as st
import utils
import state
import plotly.express as px
import pandas as pd

faviconPath = "../assets/fundIcon.jpeg"
st.set_page_config(page_title="Ghosal Fund - Passive Portfolio", layout="wide", page_icon=faviconPath)
#st.title("Passive Portfolio")

# Fetch Data
with st.spinner("Loading..."):
    activePerformance, passivePerformance, portfolioComparisons, activeWeeklyComparisons, passiveWeeklyComparisons = state.load_portfolio_data()
    fundData = utils.getCachedData(cacheName = "passiveData")
    benchmarkData = utils.getCachedData(cacheName = "benchmarkData")
    materialsData = utils.getSectorPerformance(fundData, 'Materials', benchmarkData, True)
    techData = utils.getSectorPerformance(fundData, 'Technology', benchmarkData, True)
    staplesData = utils.getSectorPerformance(fundData, 'Consumer Staples', benchmarkData, True)
    industrialsData = utils.getSectorPerformance(fundData, 'Industrials', benchmarkData, True)
    energyData = utils.getSectorPerformance(fundData, 'Energy', benchmarkData, True)
    discretionaryData = utils.getSectorPerformance(fundData, 'Consumer Discretionary', benchmarkData, True)
    benchmarkPerformance, sectorBenchmarkPerformances = utils.computeBenchmarksPerformances()

st.markdown("<h1 style='text-align: center;'>Passive Portfolio</h1>", unsafe_allow_html=True)

st.markdown("#")

# Calculate the relative return
passive_relative_return = passivePerformance - benchmarkPerformance

# Determine color and formatting
passive_color = "green" if passive_relative_return > 0 else "red"
passive_sign = "+" if passive_relative_return > 0 else ""  # Add a "+" sign for positive numbers

leftColumn, rightColumn = st.columns(2)

with leftColumn:
    st.subheader("Cumulative Performance", divider="gray")
    with st.container(border=True):
        st.markdown(
            f"**Since Inception:** {passivePerformance:.2f}%&emsp;&emsp;"
            f"**Relative Return to Benchmark:** "
            f"<span style='color:{passive_color};'>{passive_sign}{passive_relative_return:.2f}%</span>",
            unsafe_allow_html=True
        )
        # Create cumulative performance plot
        cum_fig = px.line(portfolioComparisons, 
            x='Date', 
            y=['Ghosal Passive Fund', 'Benchmark (MSCI ACWI) Passive'],
            labels={'value': 'Returns (%)', 'variable': ''}
        )
        cum_fig.update_layout(
            hovermode='x unified',
            margin=dict(l=10, r=10, t=10, b=30),
            height=350,
            legend=dict(
                yanchor="top",
                y=-0.2,
                xanchor="left",
                x=-0.2
            )
        )
        cum_fig.update_traces(
            hovertemplate='%{y:.2f}%'
        )
        st.plotly_chart(cum_fig, use_container_width=True)

    st.subheader("Portfolio Analytics", divider="gray")
    with st.container(border=True):
        st.dataframe(utils.computeStatistics(fundData, benchmarkData), use_container_width=True) # fund vs benchmark

with rightColumn:
    st.subheader("Discrete Performance", divider="gray")
    with st.container(border=True):
        # Create discrete performance plot
        disc_fig = px.bar(passiveWeeklyComparisons,
            x='Date',
            y=['Ghosal Passive Fund', 'Benchmark (MSCI ACWI) Passive'],
            labels={'value': 'Weekly Returns (%)', 'Date': 'Week', 'variable': ''},
            barmode='group'
        )
        disc_fig.update_layout(
            hovermode='x unified',
            margin=dict(l=10, r=10, t=10, b=30),
            height=385,
            legend=dict(
                yanchor="top",
                y=-0.2,
                xanchor="left",
                x=-0.2
            ),
            xaxis_title="Week Commencing"
        )
        disc_fig.update_traces(
            hovertemplate='%{y:.2f}%'
        )
        st.plotly_chart(disc_fig, use_container_width=True)
        
    st.subheader("Top 10 Holdings", divider="gray")
    with st.container(border=True):
        st.table(utils.computePortfolioDistribution(fundData)) # fund vs benchmark

st.markdown("#")
st.subheader("Stock Performance", divider="gray")

# Get stock data
stockData = utils.getStocks(fundData)

# Create figure with stock performance
with st.container(border=True):
    stock_fig = px.line(stockData,
        x='Date',
        y='PercentageDiff',
        color='Holding Name',
        labels={'PercentageDiff': 'Returns (%)', 'Holding Name': 'Stock'}
    )
    stock_fig.update_layout(
        hovermode='x unified',
        showlegend=True,
        margin=dict(l=10, r=10, t=10, b=100),  
        legend=dict(
            yanchor="top",
            y=-0.2,  
            xanchor="center",  
            x=0.5,
            orientation="h"  
        ),
        height=1000
    )
    stock_fig.update_traces(
        hovertemplate='%{y:.2f}%'
    )
    st.plotly_chart(stock_fig, use_container_width=True)

st.markdown("#")
st.subheader("Sector Team Performance", divider="gray")

# Create two columns for sector dropdowns
col1, col2 = st.columns(2)

with col1:
    # Materials Sector
    with st.expander("Materials"):
        metric_col1, metric_col2, metric_col3 = st.columns(3)
        with metric_col1:
            st.metric("Weight", f"{utils.getSectorSplit(fundData, 'Materials'):.1f}%")
        with metric_col2:    
            st.metric("Return", f"{utils.getSectorPerformanceToday(fundData, 'Materials'):.2f}%")
        with metric_col3:
            materials_benchmark_return = utils.computeBenchmarksPerformances()[1].get("Materials", None)
            materials_relative_return = utils.getSectorPerformanceToday(fundData, 'Materials') - materials_benchmark_return
            mat_sign = "+" if materials_relative_return > 0 else ""
            st.metric("Relative Return", f"{mat_sign}{materials_relative_return:.2f}%")
        mat_fig = px.line(materialsData,
            x='Date',
            y=['Materials', 'Benchmark (iShares Global Materials)'],
            labels={'value': 'Returns (%)', 'variable': ''},
        )
        mat_fig.update_layout(
            hovermode='x unified',
            showlegend=True,
            margin=dict(l=10, r=10, t=30, b=10)
        )
        mat_fig.update_traces(
            hovertemplate='%{y:.2f}%'
        )
        st.plotly_chart(mat_fig, use_container_width=True)

        # Technology Sector
    with st.expander("Technology"):
        metric_col1, metric_col2, metric_col3 = st.columns(3)
        with metric_col1:
            st.metric("Weight", f"{utils.getSectorSplit(fundData, 'Technology'):.1f}%")
        with metric_col2:
            st.metric("Return", f"{utils.getSectorPerformanceToday(fundData, 'Technology'):.2f}%")
        with metric_col3:
            technology_benchmark_return = utils.computeBenchmarksPerformances()[1].get("Technology", None)
            technology_relative_return = utils.getSectorPerformanceToday(fundData, 'Technology') - technology_benchmark_return
            tech_sign = "+" if technology_relative_return > 0 else ""
            st.metric("Relative Return", f"{tech_sign}{technology_relative_return:.2f}%")
        tech_fig = px.line(techData,
            x='Date',
            y=['Technology', 'Benchmark (iShares Global Technology)'],
            labels={'value': 'Returns (%)', 'variable': ''},
        )
        tech_fig.update_layout(
            hovermode='x unified',
            showlegend=True,
            margin=dict(l=10, r=10, t=30, b=10)
        )
        tech_fig.update_traces(
            hovertemplate='%{y:.2f}%'
        )
        st.plotly_chart(tech_fig, use_container_width=True)
    
    
    # Consumer Staples Sector
    with st.expander("Consumer Staples"):
        metric_col1, metric_col2, metric_col3 = st.columns(3)
        with metric_col1:
            st.metric("Weight", f"{utils.getSectorSplit(fundData, 'Consumer Staples'):.1f}%")
        with metric_col2:
            st.metric("Return", f"{utils.getSectorPerformanceToday(fundData, 'Consumer Staples'):.2f}%")
        with metric_col3:
            staples_benchmark_return = utils.computeBenchmarksPerformances()[1].get("Consumer Staples", None)
            staples_relative_return = utils.getSectorPerformanceToday(fundData, 'Consumer Staples') - staples_benchmark_return
            staples_sign = "+" if staples_relative_return > 0 else ""
            st.metric("Relative Return", f"{staples_sign}{staples_relative_return:.2f}%")
        stap_fig = px.line(staplesData,
            x='Date',
            y=['Consumer Staples', 'Benchmark (iShares Global Consumer Staples)'],
            labels={'value': 'Returns (%)', 'variable': ''},
        )
        stap_fig.update_layout(
            hovermode='x unified',
            showlegend=True,
            margin=dict(l=10, r=10, t=30, b=10)
        )
        stap_fig.update_traces(
            hovertemplate='%{y:.2f}%'
        )
        st.plotly_chart(stap_fig, use_container_width=True)

with col2:
        # Industrials Sector
    with st.expander("Industrials"):
        metric_col1, metric_col2, metric_col3 = st.columns(3)
        with metric_col1:
            st.metric("Weight", f"{utils.getSectorSplit(fundData, 'Industrials'):.1f}%")
        with metric_col2:
            st.metric("Return", f"{utils.getSectorPerformanceToday(fundData, 'Industrials'):.2f}%")
        with metric_col3:
            industrials_benchmark_return = utils.computeBenchmarksPerformances()[1].get("Industrials", None)
            industrials_relative_return = utils.getSectorPerformanceToday(fundData, 'Industrials') - industrials_benchmark_return
            industrials_sign = "+" if industrials_relative_return > 0 else ""
            st.metric("Relative Return", f"{industrials_sign}{industrials_relative_return:.2f}%")
        ind_fig = px.line(industrialsData,
            x='Date',
            y=['Industrials', 'Benchmark (iShares Global Industrials)'],
            labels={'value': 'Returns (%)', 'variable': ''},
        )
        ind_fig.update_layout(
            hovermode='x unified',
            showlegend=True,
            margin=dict(l=10, r=10, t=30, b=10)
        )
        ind_fig.update_traces(
            hovertemplate='%{y:.2f}%'
        )
        st.plotly_chart(ind_fig, use_container_width=True)
    
    
    # Energy Sector
    with st.expander("Energy"):
        metric_col1, metric_col2, metric_col3 = st.columns(3)
        with metric_col1:
            st.metric("Weight", f"{utils.getSectorSplit(fundData, 'Energy'):.1f}%")
        with metric_col2:
            st.metric("Return", f"{utils.getSectorPerformanceToday(fundData, 'Energy'):.2f}%")
        with metric_col3:
            energy_benchmark_return = utils.computeBenchmarksPerformances()[1].get("Energy", None)
            energy_relative_return = utils.getSectorPerformanceToday(fundData, 'Energy') - energy_benchmark_return
            energy_sign = "+" if energy_relative_return > 0 else ""
            st.metric("Relative Return", f"{energy_sign}{energy_relative_return:.2f}%")
        energy_fig = px.line(energyData,
            x='Date',
            y=['Energy', 'Benchmark (iShares Global Energy)'],
            labels={'value': 'Returns (%)', 'variable': ''},
        )
        energy_fig.update_layout(
            hovermode='x unified',
            showlegend=True,
            margin=dict(l=10, r=10, t=30, b=10)
        )
        energy_fig.update_traces(
            hovertemplate='%{y:.2f}%'
        )
        st.plotly_chart(energy_fig, use_container_width=True)
    
    
    # Consumer Discretionary Sector
    with st.expander("Consumer Discretionary"):
        metric_col1, metric_col2, metric_col3 = st.columns(3)
        with metric_col1:
            st.metric("Weight", f"{utils.getSectorSplit(fundData, 'Consumer Discretionary'):.1f}%")
        with metric_col2:
            st.metric("Return", f"{utils.getSectorPerformanceToday(fundData, 'Consumer Discretionary'):.2f}%")
        with metric_col3:
            discretionary_benchmark_return = utils.computeBenchmarksPerformances()[1].get("Consumer Discretionary", None)
            discretionary_relative_return = utils.getSectorPerformanceToday(fundData, 'Consumer Discretionary') - discretionary_benchmark_return
            discretionary_sign = "+" if discretionary_relative_return > 0 else ""
            st.metric("Relative Return", f"{discretionary_sign}{discretionary_relative_return:.2f}%")
        disc_fig = px.line(discretionaryData,
            x='Date',
            y=['Consumer Discretionary', 'Benchmark (iShares Global Consumer Discretionary)'],
            labels={'value': 'Returns (%)', 'variable': ''},
        )
        disc_fig.update_layout(
            hovermode='x unified',
            showlegend=True,
            margin=dict(l=10, r=10, t=30, b=10)
        )
        disc_fig.update_traces(
            hovertemplate='%{y:.2f}%'
        )
        st.plotly_chart(disc_fig, use_container_width=True)

        
        
st.markdown("#")
st.subheader("Portfolio Distribution", divider="gray")

# Get geographic split data
geoData = utils.getGeographicSplit(fundData)

# Create columns for distribution charts
dist_col1, dist_col2 = st.columns(2)

with dist_col1:
    st.subheader("Geographic Distribution")
    with st.container(border=True):
        geo_fig = px.pie(geoData,
            values='Percentage',
            names='Country',
        )
        geo_fig.update_layout(
            showlegend=True,
            margin=dict(l=10, r=10, t=30, b=10),
            height=400
        )
        geo_fig.update_traces(
            textposition='inside',
            textinfo='percent+label'
        )
        st.plotly_chart(geo_fig, use_container_width=True)

with dist_col2:
    st.subheader("Sector Distribution")
    with st.container(border=True):
        # Create sector data
        sectorData = pd.DataFrame({
            'Sector': ['Materials', 'Technology', 'Consumer Staples', 'Industrials', 'Energy', 'Consumer Discretionary'],
            'Percentage': [
                utils.getSectorSplit(fundData, 'Materials'),
                utils.getSectorSplit(fundData, 'Technology'),
                utils.getSectorSplit(fundData, 'Consumer Staples'),
                utils.getSectorSplit(fundData, 'Industrials'),
                utils.getSectorSplit(fundData, 'Energy'),
                utils.getSectorSplit(fundData, 'Consumer Discretionary')
            ]
        })
        
        sector_fig = px.pie(sectorData,
            values='Percentage',
            names='Sector'
        )
        sector_fig.update_layout(
            showlegend=True,
            margin=dict(l=10, r=10, t=30, b=10),
            height=400
        )
        sector_fig.update_traces(
            textposition='inside',
            textinfo='percent+label'
        )
        st.plotly_chart(sector_fig, use_container_width=True)

if reloadCache := st.button("Reload Cache"):
    utils.clearCache("passiveData")
