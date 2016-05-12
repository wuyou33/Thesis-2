#!/usr/bin/python3

import sys, os, re, copy, logging
from optparse import OptionParser
from bs4 import BeautifulSoup, Comment
from jinja2 import Environment, PackageLoader

MLX_TEMPLATE = '''
<!doctype html>
<html lang="en">
    <head> 
        <title>{{ title }}</title>
        
        <!-- MATLAB Styles, real semantic and all. Good job guys ! -->
        {{ matlab_styles }}

        <!-- Our Styles -->
        <style>
            body { background-color: #666666; }

            .content_wrapper { 
                max-width: 1280px; 
                margin: 20px auto; 

                overflow: hidden;
                border-radius: 5px; 
            }

            .content_row {
                background-color: #fff;
                width: 100%;
                border-bottom: solid 1px #CCCCCC;
            }

            .pane {
                box-sizing: border-box;
                float: left;
                width: 50%;
            }

            a.local-anchor { text-decoration: none; }
            a.local-anchor:hover { text-decoration: underline; }

            .pane:first-child { padding: 20px 10px 20px 20px; }
            .pane:last-child  { padding: 20px 20px 10px 20px; }

            .clearfix       { overflow: auto; }
            .clearfix:after { clear:both; }

            .LineNodeBlock.contiguous { overflow: auto;  }
        </style>

        <script type="text/x-mathjax-config">
            MathJax.Hub.Config({
              tex2jax: {inlineMath: [['$','$'], ['\\\\(','\\\\)']]}
            });
        </script>

        <script type='text/javascript' src='https://cdn.mathjax.org/mathjax/latest/MathJax.js?config=TeX-AMS-MML_HTMLorMML'></script>
    </head> 
    <body> 
    <div class="content_wrapper">
    {% for section in sections %}
        <div class="content_row clearfix">
            <div class="pane">{{ section[0].decode('utf-8')|safe }}</div>
            <div class="pane">  
                {% for output in section[1] %} 
                    {{ output.decode('utf-8')|safe }}
                {% endfor %}
            </div>         
        </div>
    {% endfor %}
    </div>
    </body> 
</html>
'''


def convert(in_file, out_file):
    # Load the template used to render the output file. 
    jinja = Environment(loader=PackageLoader('mlx_formatter', 'templates'))
    template = jinja.from_string(MLX_TEMPLATE)

    # Load the html generated by matlab and extract the parts we need.
    with open(in_file) as file:
        html_source = file.read()

    soup = BeautifulSoup(html_source, 'html.parser', from_encoding="utf-8")
    style = soup.head.style

    # First we run operations that operate on the entire document
    convert_equations(soup)
    make_titles_into_anchors(soup)

    # Then we run operations that split it up

    # Process sections to extract their outputs and put them in a second pane
    sections = soup.find_all('div', 'SectionBlock')
    logging.debug('Located %d sections', len(sections))
    
    split_sections = [process_section(s) for s in sections]

    # Extrac the document title. If the mlx document does not have a title then this string  will 
    # be empty. 
    title = soup.title.string; 

    with open(out_file, 'w') as file:
        file.write(template.render(
            title = title, matlab_styles=style, sections=split_sections))

def make_titles_into_anchors(soup):
    titles = soup.find_all(['h1', 'h2'])

    title_count = 1
    for title in titles:
        logging.debug('Making anchor link for title: %s', title.string)
        title.wrap(soup.new_tag(
            'a', id='anchor{}'.format(title_count), href='#', **{'class': 'local-anchor'}))
        title_count += 1

def convert_equations(soup):
    # Extact text contained between ##### SOURCE BEGIN ##### and ##### SOURCE END ##### 
    # dicard all non-comment lines.
    comments =  soup.findAll(text=lambda text:isinstance(text, Comment))

    ml_source = None
    for comment in comments: 
        comment_text = str(comment).strip()
        if comment_text.startswith('##### SOURCE BEGIN #####'):
            ml_source = comment_text

    if not ml_source: 
        print('\nWARNING: Unable to locate the original MATLAB source in the file. Equations will not be substituted\n')
        return

    # Equations will only be contained in comment lines. To extract them we discard all non-comment 
    # lines and trim whitespace. 
    code_lines = [l.strip() for l in ml_source.split('\n')]
    comment_lines = [l for l in code_lines if len(l) > 0 and l[0] == '%']

    # extract equations with regexp
    # The rege looks for blocks of 1 or more $ characters followed by any other character one or 
    # more times non-greedily until it finds one or more $ characters. 
    #
    # This is not a robust regex !! It can beak in so many different ways. I'll  try to fix them as 
    # I come across them.
    equation_re = re.compile(r'(\$+.+?\$+)', re.DOTALL)
    equations = equation_re.findall('\n'.join(comment_lines))

    # extract eqution divs. These do not have a distinctive class. The best way of finding them 
    # seems to be extracting all the image tags and discarding the ones that have a .figureImage 
    # class
    def images_that_are_not_figures(tag):
        if tag.name == 'img' and not tag.has_attr('class'):
            return True
        else:
            return False

    equation_image_divs = soup.find_all(images_that_are_not_figures)

    if len(equations) != len(equation_image_divs):
        logging.warning(
            'I found a different number of equations in the embedded source code and in the html. '
            'Since I can\'t match them to one another I won\'t be replacing the equtions in the '
            'ouput.\n')

        logging.debug('Equations identified in input file: ')
        for eq in equations:
            print('  => ', eq)


    # We have the same number of equations and images ! We can replace them all !
    logging.debug('Isolated {} equations in the embedded source and {} equation images'.format(
        len(equations), len(equation_image_divs)))

    for i in range(len(equations)): 
        # We need to detect whether this is an inline equation or not. maybe detect the presence of siblings ?

        new_eq = soup.new_tag('span', class_='math')
        new_eq.string = equations[i]

        # If the equation spans multiple lines we need to remove '%' characters inside it. 
        # The regex that does this looks for a litteral % character preceded by a newline (\n)
        new_eq.string = re.sub('(?<=\n)\%', ' ', new_eq.string)
        new_eq.string = new_eq.string.replace('\n', '')
        
        equation_image_divs[i].replace_with(new_eq)


def process_section(section):
    """
    Sections contain text, code, equations, textual outputs and figures. We want to keep the text, 
    code and equations in the left pane and the textual output ad figure in the right pane. 
    """

    output_paragraphs = section.find_all('div', 'outputParagraph')
    # print('  Found {} output blocks'.format(len(output_paragraphs)))

    outputs = []
    for output in output_paragraphs:
        outputs.append(output.extract())

    # For some reason the last of line of code before a figure sometimes has an extra .output class
    # we need to remove it. 
    code_with_output_class = section.select('.inlineWrapper.outputs')
    # print('  Found {} code lines with the .output class'.format(len(code_with_output_class)))

    for line in code_with_output_class:
        line.attrs['class'] = 'inlineWrapper'
   
    # To improve readability we also remve empty lines from the start and end of each code block
    for code_block in section.select('.LineNodeBlock'):
        trim_empty_code_lines(code_block)

    # By default Beautiful soup will convert all html entities to equivalent unicode characters. To 
    # retrieve the original entities (especially &nbsp) we need to reencode the data.
    outputs = [o.encode_contents(formatter='html') for o in outputs]
    section = section.encode_contents(formatter='html')

    return (section, outputs)


def trim_empty_code_lines(section):
    lines = section.find_all('div', 'inlineWrapper')

    for line in lines:
        if line.get_text() == '': 
            line.extract()
        else:
            break

    for line in lines[::-1]:
        if line.get_text() == '':
            line.extract()
        else:
            break


if __name__ == '__main__':
    opts = OptionParser()
    opts.add_option('-i', '--input-file', 
        action='store', type='string', dest='in_file', help='File to be processed')
    opts.add_option('-o', '--output-file', 
        action='store', type='string', dest='out_file', help='File that will contain the new html')
    opts.add_option('-d', '--debug', 
        action='store_true', dest='debug', help='Enable verbose output')

    (options, args) = opts.parse_args()

    logLevel = logging.INFO
    if options.debug:
        logLevel = logging.DEBUG
    logging.basicConfig(level=logLevel, format='[%(levelname)s] %(message)s')

    if not options.in_file and len(args) == 1: 
        options.in_file = args[0]
    else: 
        opts.print_help()

    # Generate the output path if it is not specified.
    #   given an input path /foo/bar/test.html the output will be /foo/bar/test_mlx.html
    if not options.out_file: 
        dirname, file = os.path.split(options.in_file)
        filename, ext = os.path.splitext(file)
        options.out_file = os.path.join(dirname, filename + '_mlx' + ext)

    convert(options.in_file, options.out_file)
    logging.info('mlx_formatter: saved generated file to %s', options.out_file)
