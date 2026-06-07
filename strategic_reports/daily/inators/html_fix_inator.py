#
# load useful libraries
#
import re
from bs4 import BeautifulSoup

#
# See if we already have a title in the HTML
#
def get_title_if_it_exists(html_content : str) -> str | None:
    soup = BeautifulSoup(html_content, 'html.parser')
    title = None
    try:
        title = soup.find('title').text.strip()
    except:
        pass
    return title

#
# Estimate a title from the first header item found (grouped by header degree)
#
def estimate_the_title_from_first_header(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    title = None
    success = False

    for tag in ['h1', 'h2', 'h3']:
        first_h = soup.find(tag)
        if first_h != None:
            success = True
            title = first_h.text.strip()
            break

    if success:
        return title
    else:
        return None

#
# Figure out what to do about the title
#
def construct_title(
    html_content : str,
    title : str = None,
) -> str:
    html_title = ''
    
    if title == None:
        title = get_title_if_it_exists(html_content)
        if title == None:
            title = estimate_the_title_from_first_header(html_content)

    if title != None:
        html_title = '<title>' + title + '</title>'

    html_title = re.sub(' +', ' ', html_title)
    return html_title

#
# Insert the DOCTYPE header
#
def insert_doctype(html_content : str) -> str:
    if html_content.find('<!DOCTYPE html>') == -1:
        return '<!DOCTYPE html>\n' + html_content
    else:
        return html_content

#
# Wrap the content with the body HTML tag
#
def wrap_tag_body(html_content : str) -> str:
    if html_content.find('<body>') == -1:
        return '<body>\n' + html_content + '\n</body>'
    else:
        return html_content

#
# Create an HTML head section
#
def construct_head(
    html_content : str,
    ai_content : str = 'none',
    include_ai_content_attribute : bool = True,
    title : str = None,
) -> str:

    html_head = '<head>\n'

    html_title = construct_title(html_content, title = title)
    if html_title.strip() != '':
        html_head += '\t' + html_title + '\n'

    if include_ai_content_attribute:
        html_head += '\t' + '<meta name="ai-content" content="' + ai_content + '">' + '\n'
    
    html_head += '</head>'
    return html_head

#
# Put everything together
#
def html_fix_assemble(
    html_content: str,
    ai_content : str = 'none',
    title : str = None,
) -> str:

    html_content = wrap_tag_body(html_content)
    
    html_head = None
    if html_content.find('<head>') == -1:
        html_head = construct_head(html_content, ai_content = ai_content, title = title)
    html_content = html_head + '\n' + html_content

    html_content = insert_doctype(html_content)
    
    return html_content
