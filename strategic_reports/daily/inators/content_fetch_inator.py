
#
# load useful libraries
#
import pandas as pd
import feedparser
import datetime
from time import mktime
import html_to_markdown
import json
import multiprocessing

#
# define a function to load and deduplicate the feeds list
#
def load_and_deduplicate_the_feeds_list(feeds_list_json_filename):

    list_feeds = None
    with open(feeds_list_json_filename) as f:
        list_feeds = json.load(f)['feeds']

    #
    # remove duplicates though they are unlikely
    #
    list_feeds = (
        pd.DataFrame(list_feeds)
        .drop_duplicates()
        .reset_index(drop = True)
        .to_dict(orient = 'records')
    )

    #
    # enumerate the list elements
    #
    for i in range(0, len(list_feeds)):
        list_feeds[i]['index'] = i
    
    return list_feeds

#
# retrieve the content from the feeds
#
def retrieve_content_from_feeds(
        list_feeds,
        number_of_feed_retrieval_processes = 15,
        hours_cutoff = 24,
):
    # crude
    for item in list_feeds:
        item['hours_cutoff'] = hours_cutoff

    with multiprocessing.Pool(processes = number_of_feed_retrieval_processes) as pool:
        retrieved_feed_list = pool.map(convert_feed_to_df, list_feeds)

    return retrieved_feed_list

#
# define function to convert RSS results to a Pandas dataframe
#
def convert_feed_to_df(dict_feed_identifier):

    dict_to_return = dict_feed_identifier.copy()
    
    #
    # Parse the RSS feed
    #
    try:
        feed = feedparser.parse(dict_feed_identifier['url'])
    except Exception as e:
        dict_to_return['df'] = pd.DataFrame()
        dict_to_return['exception'] = str(e)
        return dict_to_return

    #
    # retrieve the number of hours beyond which to cut off results (CRUDE)
    #
    hours_cutoff = dict_feed_identifier['hours_cutoff']

    #
    # Compute the cutoff time
    #
    dt_cutoff = datetime.datetime.now() - datetime.timedelta(hours = hours_cutoff)
    
    #
    # Organize feed information into a Python dictionary
    #
    list_dict_entry = []
    for entry in feed.entries:

        try:
            time_struct = entry.published_parsed
            dt = datetime.datetime.fromtimestamp(mktime(time_struct))
        except:
            continue

        if dt < dt_cutoff:
            continue

        #
        # need a plan for if this fails
        #
        #assert isinstance(entry.content, list)
        #assert len(entry.content) == 1

        try:
            i = 0
            content = entry.content[i]['value']
            if entry.content[i]['type'].strip() == 'text/html':
                content = html_to_markdown.convert(content).content
        
            dict_entry = {
                'title' : str(entry.title).strip(),
                'summary_from_feed' : str(entry.summary).strip(),
                'content' : content.strip(),
                'link' : str(entry.link).strip(),
                'publish_date' : str(dt),
            }
            list_dict_entry.append(dict_entry)
        except:
            continue

    dict_to_return['df'] = pd.DataFrame(list_dict_entry)
    return dict_to_return

#
# for each dataframe in the list item dictionary, deduplicate it
#
# In theory, this shouldn't be necessary, but it ensures quality
#
def deduplicate_each_dataframe(list_feeds):
    new_list_feeds = []
    for feed in list_feeds:

        new_dict = {}
        for key in feed.keys():
            new_dict[key] = feed[key]
        
        df = feed['df'].copy().drop_duplicates().reset_index(drop = True)
        new_dict['df'] = df
        new_list_feeds.append(new_dict)
    return new_list_feeds

#
# wrapper
#
def fetch_rss_content(
    feeds_list_json_filename,
    number_of_feed_retrieval_processes = 15,
    hours_cutoff = 24,
):
    list_feeds = load_and_deduplicate_the_feeds_list(feeds_list_json_filename)

    retrieved_list_feeds = retrieve_content_from_feeds(
        list_feeds,
        number_of_feed_retrieval_processes = number_of_feed_retrieval_processes,
        hours_cutoff = hours_cutoff,
    )

    deduplicated_list_feeds = deduplicate_each_dataframe(retrieved_list_feeds)

    return deduplicated_list_feeds

#
# A useful (but not perfect) QA tool
#
def qa_fetch_rss_content(list_feeds):
    qa_success = False
    
    for feed in list_feeds:
        df = feed['df']
        if len(df.index) > 0:
            qa_success = True
            break

    if qa_success:
        for key in [x for x in feed.keys() if not x == 'df']:
            print(key + ': ' + str(feed[key]))
        print()
        print()
        print(df)
        print()
        print()
        print('At least one of the feeds returned content.')

    return qa_success
