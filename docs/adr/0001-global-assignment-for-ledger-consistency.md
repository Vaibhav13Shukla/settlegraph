# Use global assignment instead of independent pairwise matching

SettleGraph resolves matches under global assignment constraints because independent pairwise decisions can create impossible ledgers (for example, multiple records mapped to one counterpart). This adds implementation complexity but preserves financial consistency and makes exception handling explicit instead of silently corrupting the output.

