# Daily Strategic Review

This AI workflow produces a daily report recommending business, entrepreneurship, and career strategies with respect to several critical operational arenas. It does not cover warfighting strategy despite the inclusion of defense industry recommendations.

## Rationale

Reading hundreds of news articles and mentally synthesizing them into strategic recommendations proves beyond the time constraints of most individuals, who simply have better things to with their time. While LLM-generated strategic summarization is no substitute for human-generated analysis, it helps reduce this burden.

While I cannot put an exact number on the percent increase, using this report every weekday increased my strategic insight substantially. 

I recommend combining the output of this tool with your own analysis and intuition, which is probably what you would do anyway. Also, this pipeline, because of its fundamental dependence on LLM technology, occassionally produces erroneous results. **Caveat emptor!** Please apply judgement and follow-up research before proceding with this tool's recommendations. Use at your own risk. Use your wisdom and judgement; don't hold me liable for anything!

## When to run this

This tool only considers news articles produced in the 24-hours before runtime, though this value is configurable if you want to change it. Because many news sources do not publish on weekends, expect less output value on weekends.

## How to run this

I will add a "requirements.txt" file for pip dependencies to this document in the near future.

First set two vital environment valiables:

"STRATEGIC_REPORTS_HOME" is the path to the "strategic-reports" parent directory within this repository. For example, suppose you have checked out this repository into the parent directory ```$HOME/Desktop/projects```. Then the command to set this environment variable would be:

```
export STRATEGIC_REPORTS_HOME=$HOME/Desktop/projects/AI/AI-workflows/strategic-reports
```

This pipeline calls Ollama models hosted at [ollama.com](ollama.com), mostly because running locally on my personal machine caused my overhead lights to dim while during the LLM portions of the workflow! So rather than absorb the additional electrical cost burden and even burning out my local GPU prematurely by running these LLMs locally, I pointed the pipeline to a LLMs served by an external Ollama provider. As such, you need to set the API key for this provider:

```
export OLLAMA_API_KEY=...
```

The pipeline consists of a series of Jupyter notebooks. You can kick it off with papermill:

```
papermill $STRATEGIC_REPORTS_HOME/strategic_reports/daily/pipeline-all-topics.ipynb /tmp/pipeline-all-topics.ipynb
```

## Output

Output will be posted to ```$STRATEGIC_REPORTS_HOME/output/daily```. The ```$STRATEGIC_REPORTS_HOME/output/daily/strategic-report``` subdirectory contains the final reports in HTML format. ```index.html``` contains the strategic recommendations, while the other HTML files contain the per-topic LLM-generated summaries that the final strategic recommendations were computed from.
