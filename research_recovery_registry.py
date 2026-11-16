"""Explicit pinned recovery versions; unknown markers fail closed."""
import importlib
REGISTRY = {
 '43788629748': ('research_manifest_recovery_20261116', '7bec9bf887d71c193b1d22da812488af56fe1298f40e265e6201100a20ed12f7'),
 '43544491120': ('research_manifest_recovery', 'ec076f46281b3ac098bb8dedbc5688304aef2dadfff23d92ffdc51c3d5e79b9a'),
 '43666191271': ('research_manifest_recovery_20261115', '04269c6d07cf04ae9f95d78320e7b73d16cf096c3134ed63939df40d9704e50b'),
}
def resolve(record):
 marker=record.get('research_recovery', {})
 name,digest=REGISTRY.get(marker.get('source_run'), (None,None))
 if name is None or marker.get('bundle_sha256') != digest:
  raise ValueError('Unknown pinned research recovery')
 return importlib.import_module(name)
def recovered_manifest(record,recent):
 return resolve(record).recovered_manifest(record,recent)
def write_source_packets(record,jobs):
 return resolve(record).write_source_packets(jobs)

