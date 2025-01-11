# From https://medium.com/@jameskabbes/sending-imessages-with-python-on-a-mac-b77b7dd6e371
on run {phone, message}
tell application "Messages"
	set targetService to 1st service whose service type = iMessage
	set targetBuddy to buddy phone of targetService
	send message to targetBuddy
end tell
end run
