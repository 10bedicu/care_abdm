#!/usr/bin/env python

"""The setup script."""

from setuptools import find_packages, setup

with open("README.md") as readme_file:
    readme = readme_file.read()

with open("HISTORY.rst") as history_file:
    history = history_file.read()

requirements = [
    "requests",
    "celery",
    "django",
    "djangorestframework",
    "django-environ",
    "django-filter",
    "jsonschema",
    "fhir.resources>=7.1.0,<8.0.0",
    "fastecdsa==2.3.2",
    "pycryptodome",
]

test_requirements = []

setup(
    author="Open Healthcare Network",
    author_email="info@ohc.network",
    python_requires=">=3.6",
    classifiers=[
        "Development Status :: 2 - Pre-Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Natural Language :: English",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
    ],
    description="Nothing Much",
    install_requires=requirements,
    license="MIT license",
    long_description=readme + "\n\n" + history,
    include_package_data=True,
    keywords="abdm",
    name="abdm",
    packages=find_packages(include=["abdm", "abdm.*"]),
    package_data={
        "abdm": ["data/*.json"],
    },
    test_suite="tests",
    tests_require=test_requirements,
    url="https://github.com/ohcnetwork/care_abdm",
    version="0.2.0",
    zip_safe=False,
)
