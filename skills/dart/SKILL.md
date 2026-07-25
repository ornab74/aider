---
name: dart
description: Work on Dart and Flutter projects with analyzer-driven, widget-aware edits.
tags: [dart, flutter, widgets]
---
# Workflow
Inspect `pubspec.yaml`, run `dart analyze` or `flutter analyze`, locate the smallest widget or service boundary, and run focused tests.

# Quality
Prefer immutable widgets, explicit async error handling, and stable state ownership. Avoid rebuilding large widget trees unnecessarily.

# Safety
Review package provenance before `flutter pub add` or `dart pub add`.
