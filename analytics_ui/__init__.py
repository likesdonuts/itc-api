"""The analytics app's UI layer: data/analytics/ in, site_analytics/ out.

    bundle.py   the entities and who-acted-for-whom, packed for the browser
    page.py     the one page: HTML, CSS and the script that draws every view
    render.py   writes site_analytics/index.html and site_analytics/data.js

A separate app from the case tracker (analytics_server.py, ITC
Analytics.bat): it reads what the analytics data layer wrote and never
touches the tracker's pages.
"""
