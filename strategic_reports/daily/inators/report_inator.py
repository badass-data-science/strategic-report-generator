import markdown
import markdown_strings
import datetime

#
# Given a dictionary containing a list of article summaries and tags, produce the
# per-article summary report
#
def produce_article_summary_markdown_report(dict_summaries_tags, title = None, preamble = True, articles_key = 'articles'):

    list_report_summaries = []
    
    if title != None:
        list_report_summaries.append('# Daily Article Summaries:  *' + title + '*\n')
    
    if preamble:
        list_report_summaries.append('**Updated:** ' + str(datetime.datetime.now()).split('.')[0] + '\n')
        list_report_summaries.append('*Summaries and tags are LLM-generated from the articles below:*\n')
    
    for item in dict_summaries_tags[articles_key]:
        item_markdown = ''
        item_markdown += '##' + ' [' + markdown_strings.esc_format(item['title'], esc = True).replace('$', '\\$').strip() + '](' + item['link'] + ')' + '\n'
        for bullet_point in item['summary']:
            item_markdown += '* ' + markdown_strings.esc_format(bullet_point, esc = True).replace('$', '\\$').strip() + '\n'
        item_markdown += '\n'
        item_markdown += '**Tags:** ' + ', '.join(sorted([markdown_strings.esc_format(t, esc = True).strip().replace('$', '\\$') for t in item['tags']])) + '\n'
        item_markdown += '\n'
        item_markdown += '**Publish Date:** ' + item['publish_date'] + '\n'
        list_report_summaries.append(item_markdown)

    report_summaries = '\n'.join(list_report_summaries)

    return report_summaries

#
# Produce the overall strategic report
#
def produce_strategic_markdown_report(
    list_directories_and_titles,
    directory_output_root,
    report_header_level = '#',
):
    report = report_header_level + ' Emily\'s Strategic Review\n'
    report += '\n'
    report += '**How to use this report:**  This (mostly) daily report recommends business, entrepreneurship, and career strategies with respect to several critical operational arenas. It does not cover warfighting strategy despite the inclusion of defense industry recommendations.\n'
    report += '\n'
    report += '**Updated:** ' + str(datetime.datetime.now()).split('.')[0] + '\n'
    report += '\n'
    report += '*LLM-generated analysis... because it scales. Occasionally human-reviewed.* ***Caveat emptor!***\n'
    report += '\n'

    report += report_header_level + '# Topics\n'
    report += '\n'
    
    for item in list_directories_and_titles:

        subdirectory = item['slug']
        title = item['title']
        
        try:
            # maybe this filename should be a variable
            with open(directory_output_root + '/' + subdirectory + '/report_strategy.md') as f:
                lines = ['* ' + x.strip()[2:] for x in f.readlines() if not x.strip() == '']

            lines_new = []
            for entry in lines:
                list_sentences = [x.strip() for x in entry.split(';')]
                if len(list_sentences) == 2:
                    side_left = list_sentences[0]
                    side_right = list_sentences[1][:1].upper() + list_sentences[1][1:]
                    lines_new.append('. '.join([side_left, side_right]))
                else:
                    lines_new.append(entry)
                    
        except:
            continue

        report += report_header_level + '##' + ' ' + title + '\n'
        report += '\n'
        report += '\n\n'.join(x.strip() for x in lines_new) + '\n'
        report += '\n'
        report += '[Source Material](' + subdirectory.replace('feeds_', '') + '_summaries.html)' + '\n'
        report += '\n'

    report = report.strip()
    return report

#
# convert Markdown to HTML
#
def convert_markdown_to_html(md):
    return markdown.markdown(md).replace('\\$', '$')

#
# write an HTML file to disk
#
def write_html(html, output_file):
    with open(output_file, 'w') as f:
        f.write(html + '\n')
