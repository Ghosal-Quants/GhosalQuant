import streamlit as st
import state
import plotly.express as px
import utils

# Load performance data
activePerformance, passivePerformance, portfolioComparisons, activeWeeklyComparisons, passiveWeeklyComparisons = state.load_portfolio_data()
benchmarkPerformance, sectorBenchmarkPerformances = utils.computeBenchmarksPerformances()

faviconPath = "./assets/fundIcon.jpeg"
# Set up the Streamlit app layout
st.set_page_config(page_title="Ghosal Investment Fund", layout="wide", page_icon=faviconPath)
    
# Title
st.markdown("<h1 style='text-align: center;'>Ghosal Investment Fund</h1> <br>", unsafe_allow_html=True)

st.markdown("""
<p style='text-align: center; font-size:20px;'>Welcome to the <b>Ghosal Investment Fund</b>. This platform showcases the performance and initiatives of Lancaster University's student-led investment fund.
Explore our key metrics, strategies, and projects designed to achieve sustainable financial growth and educational excellence.<br><br>
</p>
""",unsafe_allow_html=True)

leftColumn, rightColumn = st.columns(2)

# Create interactive plots with Plotly
active_fig = px.line(portfolioComparisons, 
    x='Date', 
    y=['Ghosal Active Fund', 'Benchmark (MSCI ACWI)'],
    labels={'value': 'Returns (%)', 'variable': ''}
)
active_fig.update_layout(
    hovermode='x unified',
    margin=dict(l=10, r=10, t=10, b=30),
    height=450,
    legend=dict(
        yanchor="top",
        y=-0.1,
        xanchor="left",
        x=0.0
    )
)
active_fig.update_traces(
    hovertemplate='%{y:.2f}%'
)

passive_fig = px.line(portfolioComparisons, 
    x='Date', 
    y=['Ghosal Passive Fund', 'Benchmark (MSCI ACWI) Passive'],
    labels={'value': 'Returns (%)', 'variable': ''}
    
)
passive_fig.update_layout(
    hovermode='x unified',
    margin=dict(l=10, r=10, t=10, b=30),
    height=450,
    legend=dict(
        yanchor="top",
        y=-0.1,
        xanchor="left",
        x=0.0
    )
)
passive_fig.update_traces(
    hovertemplate='%{y:.2f}%'
)

# Calculate the relative return
active_relative_return = activePerformance - benchmarkPerformance
passive_relative_return = passivePerformance - benchmarkPerformance

# Determine color and formatting
active_color = "green" if active_relative_return > 0 else "red"
active_sign = "+" if active_relative_return > 0 else ""  # Add a "+" sign for positive numbers

# Determine color and formatting
passive_color = "green" if passive_relative_return > 0 else "red"
passive_sign = "+" if passive_relative_return > 0 else ""  # Add a "+" sign for positive numbers

with leftColumn:
    st.markdown("""
        <a href="Active_Portfolio" style="text-decoration: none; color: inherit;">
            <h3>Active Portfolio Performance</h3>
        </a>
    """, unsafe_allow_html=True)
    st.markdown(
    f"**Since Inception:** {activePerformance:.2f}%&emsp;&emsp;"
    f"**Relative Return to Benchmark:** "
    f"<span style='color:{active_color};'>{active_sign}{active_relative_return:.2f}%</span>",
    unsafe_allow_html=True
    )
    st.plotly_chart(active_fig, use_container_width=True)

with rightColumn:
    st.markdown("""
        <a href="Passive_Portfolio" style="text-decoration: none; color: inherit;">
            <h3>Passive Portfolio Performance</h3>
        </a>
    """, unsafe_allow_html=True)
    st.markdown(
    f"**Since Inception:** {passivePerformance:.2f}%&emsp;&emsp;"
    f"**Relative Return to Benchmark:** "
    f"<span style='color:{passive_color};'>{passive_sign}{passive_relative_return:.2f}%</span>",
    unsafe_allow_html=True
    )
    st.plotly_chart(passive_fig, use_container_width=True)


# About section

col1, col2 = st.columns([1, 1])
with col1:
    st.markdown(
        """
        <div style='text-align: center;'>
            <br><br>
            <h4><strong>Stay Connected</strong></h4>
            <p>
                Learn more about our work and keep up with our latest updates by following us on social media:
            </p>
            <ul style='list-style:none; padding-right:53px;'>
                <li><strong>LinkedIn</strong>: <a href='https://www.linkedin.com/feed/' target='_blank'>LU Ghosal Fund</a></li>
                <li><strong>Instagram</strong>: <a href='https://www.instagram.com/luifsoc/' target='_blank'>@luifsoc</a></li>
            </ul>
        </div>
        """,
        unsafe_allow_html=True
    )
with col2:
    st.markdown(
        """
        <div style="text-align: center; margin-top: 20px;">
            <img src="https://media.licdn.com/dms/image/v2/C4E0BAQGxZKXXQicK0w/company-logo_200_200/company-logo_200_200/0/1631368530520/luifs_investment_fund_logo?e=2147483647&v=beta&t=O-JOUaRjGngJBU-i465jAKMnb2hB6OHL_68srmnBTKs" 
                 style="width: 250px; height: auto;">
        </div>
        """,
        unsafe_allow_html=True
    )
