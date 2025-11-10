import streamlit as st
import pandas as pd
import utils

faviconPath = "./assets/fundIcon.jpeg"
st.set_page_config(page_title="Ghosal Fund - Showcase", layout="wide", page_icon=faviconPath)
#st.title("Fund Showcase")

potwData = utils.getPitchesOfTheWeek()
numRows = potwData.shape[0]

st.markdown("<h1 style='text-align: center;'>Fund Showcase</h1>", unsafe_allow_html=True)

st.markdown(
    """
    <div style="max-width: 1000px; margin: 0 auto; text-align: justify;">
        <br>
        <h2>Pitch of the Week</h2>
        <p>
            After each Investment Committee, the management team come together to discuss the pitches from the week and 
            how the pitched companies could fit into our portfolio. Throughout this discussion, we vote upon the 
            <B>Pitch of the Week</B>. This is awarded based on a number of criteria: the choice of a good, truly 
            undervalued company; the production of a watertight valuation for said company; the quality of the slide 
            deck; the quality of the presentation from the Team's members.
        </p>
        <p>
            Below, we list the winners of pitch of the week in the 2024/25 academic year:
        </p>
    </div>
    """,
    unsafe_allow_html=True
)

st.markdown(
     """
    <style>
    div[data-testid="stHorizontalBlock"] {
        display: flex;
        justify-content: center;
    }
    </style>
    """,
    unsafe_allow_html=True
)
st.dataframe(potwData, hide_index=True, width=1000, height=((numRows + 1) * 35 + 3))


st.markdown(
    """
    <div style="max-width: 1000px; margin: 0 auto; text-align: justify;">
        <br>
        <h2>Pitch of the Term</h2>
        <p>
            This year, we have also decided to recognise the best pitch of the term. This pitch has been selected from
            the above list of pitch of the week winners, where they are compared against each other rather than just 
            the pitches that week.
        </p>
        <h4>
            The Pitch of the Term for the Michaelmas Term was Nextracker from the Technology team!
        </h4>
    </div>
    """,
    unsafe_allow_html=True
)

