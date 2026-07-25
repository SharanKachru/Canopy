import collections

scores = [float(l.strip()) for l in open("scores.txt")]
print("count:", len(scores))
print("min:", min(scores), "max:", max(scores))

buckets = collections.Counter()
for s in scores:
    if s >= 70:
        buckets["severe"] += 1
    elif s >= 50:
        buckets["warning"] += 1
    elif s >= 30:
        buckets["watch"] += 1
    else:
        buckets["normal"] += 1

print(buckets)
print("exactly 100.0:", sum(1 for s in scores if s == 100.0))