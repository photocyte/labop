from setuptools import setup
import sys
import os
import subprocess

def pip_install(url):
    subprocess.check_output([sys.executable, '-m', 'pip', 'install', url])

#pip_install("git+https://github.com/rpgoldman/container-ontology.git")

test_deps = [
    'nbmake',
    'pytest-xdist'
]
extras = {
    'test': test_deps,
    'paml-check': 'paml-check@ https://github.com/SD2E/paml-check/tarball/development',
}

setup(name='paml',
      description='Protocol Activity Modeling Language',
      version='1.0a1',
      install_requires=[
            'sbol3>=1.0b6',
            'sparqlwrapper>=1.8.5',
            'python-dateutil>=2.8.1',
            'sbol-factory==1.0a8',
            'requests',
            'graphviz',
            'tyto',
            'numpy',
            'openpyxl',
            'autoprotocol',
            'transcriptic',
            'requests_html',
            "container-ontology @ https://github.com/rpgoldman/container-ontology/tarball/main",
            "ipython",
            "pre-commit",
            "ipywidgets"
      ],
      tests_require=test_deps,
      extras_require=extras,
      packages=['paml', 'paml_convert', 'paml_convert.autoprotocol', 'paml_convert.markdown', 'paml_convert.opentrons', 'paml.lib', 'paml_time', 'uml'],
      package_data={'paml': ['paml.ttl', 'lib/*.ttl'],
                    'paml_convert': ['markdown/template.xlsx'],
                    'uml': ['uml.ttl'],
                    'paml_time': ['paml_time.ttl']},

      include_package_data=True
)
