+++
title = "Database Systems"
path = "databases"
render = true
+++

<span class='library-heading'>Research Paper Summaries</span>

<div class="res_div">

<div class="res_content">
<div class='res-title'>
    <span class='title'>Stage: Query Execution Time Prediction in Amazon Redshift</span>
    <span class='r-date'>(4th March, 2024)</span>
    <a href="https://arxiv.org/pdf/2403.02286">
    <img src='/Link.svg' width=15px>
    </a> 
</div>
    
<div class=content>
<span class='bold-head'>Why this paper?</span>
<span>

Redshift previously used _AutoWLM_(Auto Workload Manager) predictor for estimating query execution time.

- AutoWLM predictor takes a physical execution plan of a query as input and flattens it into a vector. Then, a lightweight XGBoost model is used to predict the query’s exec-time.
- As queries are executed in each instance, their feature vector and observed exec-time are added to the XGBoost model’s training set.

</span>
<span>It had the following issues:</span>
<span>

1. _Inaccurate estimation_
   - Due to _AutoWLM’s_ lightweight nature and simplified query featurization techniques.
2. _Cold start issue_
   - AutoWLM predictor requires a sufficient amount of executed queries as training examples, which may not be available for a new instance.
3. _Not consistent over workload/data changes_
   - Whenever the customers’ data or query workload changes, it can provide unreliable predictions until the predictor’s training set “catches up” with the change.
   - The AutoWLM predictor provides confidence intervals which are used to ensure good worst-case behavior of the changes in the cluster. These confidence intervals use simple global statistics, which can be improved.

</span>
<span class='bold-head'>What this paper offers?</span>
<span>

A new exec time predictor named **_Stage_** predictor

- A hierarchical query performance predictor.
- Hierarchical means that the predictor has 3 decision-making levels.

The 3 levels are:

1.  Execution time cache
    - Information about an incoming query is checked in the cache to do prediction based on previously observed exec-time if the query is present in the cache.
2.  A lightweight local ML model optimized for a specific DB instance with uncertainty measurement.
    - If query info is not present in the cache, the local model is used.
    - Local model (Bayesian ensemble of lightweight XGBoost models) provides a query exec-time prediction and a reliable uncertainty measure associated with the prediction with a very low inference latency.
    - Local model never learns a fully generalizable model of query performance, but it accurately predicts queries similar to the past-seen queries. Thus, acting more as a “fuzzy cache”.
    - The prediction uncertainty can be high if the local model does not have enough training examples, or if the query is very different from previously seen queries.
3.  A complex global model that is transferable across all instances in Redshift.

    - If the local model returns high prediction uncertainty, the global model is used.
    - Global model is a graph neural network that take physical query execution plan for exec-time prediction.
    - Redshift trains a single global model on a diverse set of instances to facilitate transferable knowledge of exec-time prediction across various instances, resulting in accurate prediction of exec-time of queries on unseen clusters.
    - Has non-trivial inference time, up to 100ms, thus is only used if the local model has high uncertainty and believes that query could take longer than a couple of seconds. Since the global model is rarely used, the inference overhead will be amortized.

This new exec-time predictor resulted in an average query execution latency improvement of 20%.

</span>
</div>
</div>

<!--  end -->
</div>
<span class='sub-note'>
Please go through the papers for a more detailed understanding.&nbspAlso, please raise an issue on <a href="https://github.com/nmbr7/nmbr7.github.io">GitHub</a> if you find any corrections.
</span>

<div>
</br>
<span class='library-heading'>Database Internals References</span>

<div class='library-title'>
<span class='title'>Books</span>
</div>

<div class='row'><span>- </span><span>Database Systems: The Complete Book<span class="sub-title"> by Hector Garcia-Molina, Jeffrey D. Ullman, Jennifer D. Widom</span></span><a href="https://www.amazon.com/Database-Systems-Complete-Hector-Garcia-Molina/dp/0131873253">
<img src='/Link.svg' width=15px>
</a></div>

<div class="library-subcontent">
<div class='row'>
<span>This book covers everything needed to design and implement a traditional data processing system.</span>
</div>
<div class='row'>
<span>A great reference for working with relational data systems.</span>
</div>
</div>

<div class='lib-content'>
<div class='row'><span>- </span><span>Database Internals:&nbspA Deep Dive into How Distributed Data Systems Work<span class="sub-title"> by Alex Petrov</span></span><a href="https://www.databass.dev">
<img src='/Link.svg' width=15px>
</a></div>

<div class="library-subcontent">
<div class='row'>
<span>Great book on data storage engines and related components.</span>
</div>
</div>

<div class='row'><span>- </span><span>Designing Data-Intensive Applications<span class="sub-title"> by Martin Kleppmann</span></span><a href="https://dataintensive.net">
<img src='/Link.svg' width=15px>
</a></div>

<div class="library-subcontent">
<div class='row'>
<span>A good read on designing a modern data-heavy distributed system.</span>
</div>
</div>

<div class='library-title'>
    <span class='title'>Courses</span>
</div>

<div class='lib-content'>
    <div class='row'><span>- </span><span>Intro to Database Systems <span class="sub-title"> - Carnegie Mellon University Database Group</span></span><a href="https://www.youtube.com/watch?v=vdPALZ-GCfI&list=PLSE8ODhjZXjbj8BMuIrRcacnQh20hmY9g&pp=iAQB">
    <img src='/Link.svg' width=15px>
    </a></div>
    <div class="library-subcontent">
        <div class='row'>
            <span>This course covers the basics of database systems design and internals.</span>
        </div>
        <div class='row'>
            <span>Covers different topics like query processing, transactions, storage, fault tolerance, and other common data structures & algorithms used in database systems.</span>
        </div>
    </div>
      <div class='row'><span>- </span><span>Advanced Database Systems <span class="sub-title"> - Carnegie Mellon University Database Group</span></span><a href="https://www.youtube.com/watch?v=NLycrsJ1jI8&list=PLSE8ODhjZXjYa_zX-KeMJui7pcN1rIaIJ&pp=iAQB">
    <img src='/Link.svg' width=15px>
    </a></div>
    <div class="library-subcontent">
        <div class='row'>
            <span>This course covers advanced database systems design techniques and goes deep into specific sub-systems.</span>
        </div>
        <div class='row'>
            <span>The course syllabus might differ (OLTP or OLAP systems) across semesters.</span>
        </div>
        <div class='row'>
            <span>Also, contains case studies on popular database systems and their internals.</span>
        </div>
    </div>
</div>

<div class='library-title'>
    <span class='title'>Other Resources</span>
</div>

<div class='lib-content'>
    <div class='row'><span>- </span><span>CMU Database Group YouTube</span><a href="https://www.youtube.com/@CMUDatabaseGroup">
    <img src='/Link.svg' width=15px>
    </a></div>
    <div class="library-subcontent">
        <div class='row'>
            <span>Provides different database system lectures and talks, including the above-mentioned courses.</span>
        </div>
    </div>
    <div class='row'><span>- </span><span>Jepsen Analyses</span><a href="https://jepsen.io/analyses">
    <img src='/Link.svg' width=15px>
    </a></div>
    <div class="library-subcontent">
        <div class='row'>
            <span>Contains detailed analysis on different data processing systems and their operational guarantees.</span>
        </div>
    </div>
     <div class='row'><span>- </span><span>Jack Vanlightly's&nbspWritings</span><a href="https://jack-vanlightly.com">
    <img src='/Link.svg' width=15px>
    </a></div>
    <div class="library-subcontent">
        <div class='row'>
            <span>Well-written notes and observations on data processing systems, software design and related practices.</span>
        </div>
    </div>
    <div class='row'><span>- </span><span>Build a Simple Database</span><a href="https://cstack.github.io/db_tutorial">
    <img src='/Link.svg' width=15px>
    </a></div>
    <div class="library-subcontent">
        <div class='row'>
            <span>An easy tutorial on building a DB like sqlite from scratch.</span>
        </div>
    </div>
    <div class='row'><span>- </span><span>Arpit Bhayani on YouTube </span><a href="https://www.youtube.com/@AsliEngineering/playlists">
    <img src='/Link.svg' width=15px>
    </a></div>
    <div class="library-subcontent">
        <div class='row'>
            <span>Great YouTube channel on system design and data systems.</span>
        </div>
        <div class='row'>
            <span>Contains breakdown of different data processing systems and related design techniques.</span>
        </div>
    </div>
</div>

</div>
