import os
from setuptools import setup, find_packages

project_dir = os.path.abspath(os.path.dirname(__file__))

with open(os.path.join(project_dir, 'version.txt')) as f:
    version = f.read().rstrip()

# We allow commented lines in this file
with open(os.path.join(project_dir, 'requirements.txt')) as f:
    requirements = [line.split()[0] for line in f if not line.startswith('#')]

setup(
    name="signingscript",
    version=version,
    description="TaskCluster Signing Script",
    author="Mozilla Release Engineering",
    author_email="release+python@mozilla.com",
    url="https://github.com/mozilla-releng/signingscript",
    packages=find_packages(),
    package_data={"signingscript": ["data/*"]},
    include_package_data=True,
    zip_safe=False,
    entry_points={
        "console_scripts": [
            "signingscript = signingscript.script:main",
        ],
    },
    license="MPL2",
    install_requires=requirements,
)
